from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot

from tg_activity_sender.core import ActivityMode, Blacklist, CampaignStatus, Recipient, ScheduleWindow, select_recipients
from tg_activity_sender.db import Campaign, Database, normalize_username
from tg_activity_sender.telegram_accounts import AccountManager, DeliveryClient

logger = logging.getLogger(__name__)

POST_CHAT_PRIVATE_DELAY_SECONDS = 60
IDLE_POLL_SECONDS = 20
STATUS_POLL_SECONDS = 5


class CampaignWorker:
    def __init__(
        self,
        db: Database,
        accounts: AccountManager,
        *,
        timezone: str,
        notification_bot: Bot | None = None,
        notification_chat_id: int | None = None,
    ):
        self.db = db
        self.accounts = accounts
        self.timezone = timezone
        self.notification_bot = notification_bot
        self.notification_chat_id = notification_chat_id
        self._stopped = asyncio.Event()
        self._campaign_tasks: dict[int, asyncio.Task] = {}

    async def run_forever(self) -> None:
        try:
            while not self._stopped.is_set():
                await self._sync_campaign_tasks()
                await asyncio.sleep(STATUS_POLL_SECONDS)
        finally:
            await self._cancel_all_campaign_tasks()

    def stop(self) -> None:
        self._stopped.set()
        for task in self._campaign_tasks.values():
            task.cancel()

    async def run_once(self) -> None:
        await self._sync_campaign_tasks()

    async def _sync_campaign_tasks(self) -> None:
        now = datetime.now(ZoneInfo(self.timezone))
        running_campaigns = self.db.list_campaigns(CampaignStatus.RUNNING)
        runnable_ids = {
            campaign.id
            for campaign in running_campaigns
            if self._campaign_in_schedule(campaign, now)
        }

        for campaign_id, task in list(self._campaign_tasks.items()):
            if task.done():
                self._campaign_tasks.pop(campaign_id, None)
                continue
            if campaign_id not in runnable_ids:
                task.cancel()
                self._campaign_tasks.pop(campaign_id, None)
                await self._notify(await self._campaign_stop_text(campaign_id))

        for campaign in running_campaigns:
            if campaign.id in self._campaign_tasks:
                continue
            if campaign.id not in runnable_ids:
                continue
            self._campaign_tasks[campaign.id] = asyncio.create_task(self._run_campaign(campaign.id))
            await self._notify(
                f"Бот начал работу по расписанию\n"
                f"Кампания: #{campaign.id} {campaign.name}\n"
                f"Интервал: {campaign.schedule_window}"
            )

    async def _cancel_all_campaign_tasks(self) -> None:
        tasks = list(self._campaign_tasks.values())
        self._campaign_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_campaign(self, campaign_id: int) -> None:
        try:
            while not self._stopped.is_set():
                campaign = self.db.get_campaign(campaign_id)
                if campaign is None or campaign.status != CampaignStatus.RUNNING:
                    await self._notify(await self._campaign_stop_text(campaign_id))
                    break
                if not self._campaign_in_schedule(campaign):
                    await self._notify(
                        f"Бот закончил работу по расписанию\n"
                        f"Кампания: #{campaign.id} {campaign.name}\n"
                        f"Интервал: {campaign.schedule_window}"
                    )
                    break
                did_work = await self._process_one_chat(campaign)
                if not did_work:
                    await self._sleep_checked(IDLE_POLL_SECONDS, campaign_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Campaign worker crashed for campaign %s", campaign_id)
            await self._notify(f"Ошибка воркера кампании #{campaign_id}. Подробности в логах сервера.")
        finally:
            self._campaign_tasks.pop(campaign_id, None)

    async def _process_one_chat(self, campaign: Campaign) -> bool:
        accounts = [account for account in self.db.list_accounts() if account.enabled]
        if campaign.source_account_username:
            source_username = normalize_username(campaign.source_account_username)
            accounts = [account for account in accounts if normalize_username(account.username) == source_username]
        if not accounts:
            await self._notify(f"Кампания #{campaign.id}: нет включенного аккаунта @{campaign.source_account_username or 'любой'}")
            return False

        chat_steps = self.db.get_sequence_steps(campaign.chat_sequence_id or campaign.sequence_id)
        private_steps = self.db.get_sequence_steps(campaign.private_sequence_id or campaign.sequence_id)
        if campaign.include_chats and not chat_steps:
            await self._notify(f"Кампания #{campaign.id}: чатовая цепочка пустая или не найдена, отправка остановлена")
            self.db.update_campaign_status(campaign.id, CampaignStatus.PAUSED)
            return False
        if campaign.include_private and not private_steps:
            await self._notify(f"Кампания #{campaign.id}: ЛС-цепочка пустая или не найдена, отправка остановлена")
            self.db.update_campaign_status(campaign.id, CampaignStatus.PAUSED)
            return False
        if not chat_steps and not private_steps:
            await self._notify(f"Кампания #{campaign.id}: нет шагов для отправки")
            return False

        for account in accounts:
            await self._ensure_running(campaign.id)
            client = await self.accounts.client_for(account.session_path)
            try:
                delivery = DeliveryClient(client)
                chats = await delivery.scan_group_chats(campaign.source_folder)
                await self._notify(
                    f"Кампания #{campaign.id} {campaign.name}\n"
                    f"Аккаунт: @{account.username or account.telegram_id}\n"
                    f"Папка: {campaign.source_folder or 'все группы'}\n"
                    f"Найдено чатов: {len(chats)}"
                )
                selected_chats = self._select_chats(chats, campaign)
                for chat in selected_chats:
                    await self._ensure_running(campaign.id)
                    non_team_messages = await delivery.count_recent_non_team_messages(
                        chat.id,
                        days=campaign.segment_window_days or 30,
                        team_identifiers=campaign.team_identifiers or [],
                    )
                    if not self._matches_segment(campaign, non_team_messages):
                        continue

                    chat_needs_send = bool(campaign.include_chats and chat_steps) and not self._already_sent(
                        campaign=campaign,
                        recipient_id=chat.id,
                        recipient_kind="chat",
                    )
                    private_recipients = []
                    if campaign.include_private and private_steps:
                        private_recipients = await self._private_recipients_for_chat(
                            delivery,
                            campaign,
                            chat,
                            account_telegram_id=account.telegram_id,
                        )

                    if not chat_needs_send and not private_recipients:
                        continue

                    mentions_text = self._mentions_text(private_recipients)
                    if chat_needs_send:
                        await self._send_steps(
                            delivery,
                            campaign_id=campaign.id,
                            account_telegram_id=account.telegram_id,
                            account_label=account.username or str(account.telegram_id),
                            dedupe_key=campaign.dedupe_key,
                            recipient=chat,
                            steps=chat_steps,
                            detail_prefix=f"chat; non_team_30d={non_team_messages}",
                            append_to_first_text=mentions_text,
                        )
                    if private_recipients:
                        await self._sleep_checked(POST_CHAT_PRIVATE_DELAY_SECONDS, campaign.id)
                        for participant in private_recipients:
                            await self._ensure_running(campaign.id)
                            if self._current_blacklist().matches(participant):
                                continue
                            if self._already_sent(
                                campaign=campaign,
                                recipient_id=participant.id,
                                recipient_kind="private",
                            ):
                                continue
                            await self._send_steps(
                                delivery,
                                campaign_id=campaign.id,
                                account_telegram_id=account.telegram_id,
                                account_label=account.username or str(account.telegram_id),
                                dedupe_key=campaign.dedupe_key,
                                recipient=participant,
                                steps=private_steps,
                                detail_prefix=f"participant of chat {chat.id}; non_team_30d={non_team_messages}",
                            )
                    await self._sleep_checked(campaign.delay_between_recipients_seconds, campaign.id)
                    return True
            finally:
                await client.disconnect()
        return False

    def _select_chats(self, chats, campaign: Campaign) -> list[Recipient]:
        chat_recipients = [
            Recipient(
                id=chat.id,
                kind="chat",
                username=chat.username,
                days_since_last_message=chat.days_since_last_message,
            )
            for chat in chats
            if chat.id != self.notification_chat_id
        ]
        return select_recipients(
            chat_recipients,
            mode=ActivityMode(campaign.activity_mode),
            days_threshold=campaign.days_threshold,
            include_chats=True,
            include_private=False,
            blacklist=self._current_blacklist(),
        )

    async def _private_recipients_for_chat(
        self,
        delivery: DeliveryClient,
        campaign: Campaign,
        chat: Recipient,
        *,
        account_telegram_id: int,
    ) -> list[Recipient]:
        recipients = []
        blacklist = self._current_blacklist()
        async for participant in delivery.iter_chat_participants(chat.id):
            if participant.id == account_telegram_id:
                continue
            if blacklist.matches(participant):
                continue
            if self._is_team_recipient(participant, campaign.team_identifiers or []):
                continue
            if self._already_sent(campaign=campaign, recipient_id=participant.id, recipient_kind="private"):
                continue
            recipients.append(participant)
        return recipients

    def _matches_segment(self, campaign: Campaign, non_team_messages: int) -> bool:
        if campaign.target_segment == "warm" and non_team_messages <= campaign.segment_min_non_team_messages:
            return False
        if campaign.target_segment == "cold" and non_team_messages > campaign.segment_min_non_team_messages:
            return False
        return True

    def _already_sent(self, *, campaign: Campaign, recipient_id: int, recipient_kind: str) -> bool:
        return self.db.has_delivery_log(
            campaign_id=campaign.id,
            recipient_id=recipient_id,
            recipient_kind=recipient_kind,
        ) or self.db.has_delivery_for_key(
            dedupe_key=campaign.dedupe_key,
            recipient_id=recipient_id,
            recipient_kind=recipient_kind,
        )

    async def _send_steps(
        self,
        delivery: DeliveryClient,
        *,
        campaign_id: int,
        account_telegram_id: int,
        account_label: str,
        dedupe_key: str | None,
        recipient: Recipient,
        steps,
        detail_prefix: str,
        append_to_first_text: str = "",
    ) -> None:
        try:
            appended = False
            for step in steps:
                await self._ensure_running(campaign_id)
                payload = step.payload
                if append_to_first_text and not appended and payload.get("text"):
                    payload = dict(payload)
                    payload["text"] = f"{payload['text']}\n\n{append_to_first_text}"
                    appended = True
                await self._notify(
                    f"Отправляю шаг {step.order}\n"
                    f"Кампания: #{campaign_id}\n"
                    f"Аккаунт: @{account_label}\n"
                    f"Куда: {self._recipient_label(recipient)}\n"
                    f"Тип: {self._payload_label(payload)}"
                )
                await delivery.send_payload(recipient.id, payload)
                if step.delay_after_seconds:
                    await self._sleep_checked(step.delay_after_seconds, campaign_id)
            await self._notify(
                f"Отправлено\n"
                f"Кампания: #{campaign_id}\n"
                f"Аккаунт: @{account_label}\n"
                f"Куда: {self._recipient_label(recipient)}"
            )
            self.db.create_delivery_log(
                campaign_id=campaign_id,
                account_telegram_id=account_telegram_id,
                recipient_id=recipient.id,
                recipient_kind=recipient.kind,
                status="sent",
                detail=detail_prefix,
                dedupe_key=dedupe_key,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._notify(
                f"Ошибка отправки\n"
                f"Кампания: #{campaign_id}\n"
                f"Аккаунт: @{account_label}\n"
                f"Куда: {self._recipient_label(recipient)}\n"
                f"Ошибка: {str(exc)[:450]}"
            )
            self.db.create_delivery_log(
                campaign_id=campaign_id,
                account_telegram_id=account_telegram_id,
                recipient_id=recipient.id,
                recipient_kind=recipient.kind,
                status="error",
                detail=f"{detail_prefix}: {str(exc)[:450]}",
                dedupe_key=dedupe_key,
            )

    async def _sleep_checked(self, seconds: int, campaign_id: int) -> None:
        remaining = max(int(seconds), 0)
        while remaining > 0:
            await self._ensure_running(campaign_id)
            chunk = min(STATUS_POLL_SECONDS, remaining)
            await asyncio.sleep(chunk)
            remaining -= chunk

    async def _ensure_running(self, campaign_id: int) -> None:
        campaign = self.db.get_campaign(campaign_id)
        if campaign is None or campaign.status != CampaignStatus.RUNNING:
            await self._notify(await self._campaign_stop_text(campaign_id))
            raise asyncio.CancelledError
        if not self._campaign_in_schedule(campaign):
            await self._notify(
                f"Бот закончил работу по расписанию\n"
                f"Кампания: #{campaign.id} {campaign.name}\n"
                f"Интервал: {campaign.schedule_window}"
            )
            raise asyncio.CancelledError

    def _campaign_in_schedule(self, campaign: Campaign, now: datetime | None = None) -> bool:
        moment = now or datetime.now(ZoneInfo(self.timezone))
        return ScheduleWindow.parse(campaign.schedule_window).contains(moment)

    def _current_blacklist(self) -> Blacklist:
        return Blacklist.from_entries(self.db.get_blacklist_values())

    async def _campaign_stop_text(self, campaign_id: int) -> str:
        campaign = self.db.get_campaign(campaign_id)
        if campaign is None:
            return f"Бот остановил работу\nКампания: #{campaign_id}\nПричина: кампания не найдена"
        if campaign.status == CampaignStatus.RUNNING and not self._campaign_in_schedule(campaign):
            return (
                f"Бот закончил работу по расписанию\n"
                f"Кампания: #{campaign.id} {campaign.name}\n"
                f"Интервал: {campaign.schedule_window}"
            )
        if campaign.status == CampaignStatus.PAUSED:
            reason = "пауза"
        elif campaign.status == CampaignStatus.STOPPED:
            reason = "остановлена"
        else:
            reason = f"статус {campaign.status}"
        return (
            f"Бот остановил работу\n"
            f"Кампания: #{campaign.id} {campaign.name}\n"
            f"Причина: {reason}"
        )

    def _mentions_text(self, recipients: list[Recipient]) -> str:
        mentions = []
        seen = set()
        for recipient in recipients:
            username = (recipient.username or "").strip().removeprefix("@")
            if not username:
                continue
            normalized = normalize_username(username)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            mentions.append(f"@{username}")
        return " ".join(mentions)

    def _is_team_recipient(self, recipient: Recipient, team_identifiers: list[str]) -> bool:
        identifiers = {
            str(recipient.id),
            normalize_username(recipient.username),
        }
        team = {normalize_username(item) for item in team_identifiers if item}
        return bool(identifiers & team)

    async def _notify(self, text: str) -> None:
        if self.notification_bot is None or self.notification_chat_id is None:
            return
        try:
            await self.notification_bot.send_message(self.notification_chat_id, escape(text))
        except Exception:
            logger.exception("Failed to send campaign notification")

    def _recipient_label(self, recipient: Recipient) -> str:
        username = f" @{recipient.username}" if recipient.username else ""
        return f"{recipient.kind} {recipient.id}{username}"

    def _payload_label(self, payload: dict) -> str:
        if payload.get("text") and len(payload) == 1:
            return "text"
        media_types = [key for key in ("photo", "video", "audio", "document", "voice", "video_note", "sticker", "animation") if payload.get(key)]
        if payload.get("text") and media_types:
            return "text+" + "+".join(media_types)
        return "+".join(media_types) if media_types else "message"
