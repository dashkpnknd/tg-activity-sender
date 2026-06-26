from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from tg_activity_sender.core import ActivityMode, Blacklist, CampaignStatus, Recipient, ScheduleWindow, select_recipients
from tg_activity_sender.db import Database, normalize_username
from tg_activity_sender.telegram_accounts import AccountManager, DeliveryClient


class CampaignWorker:
    def __init__(self, db: Database, accounts: AccountManager, *, timezone: str):
        self.db = db
        self.accounts = accounts
        self.timezone = timezone
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            await self.run_once()
            await asyncio.sleep(30)

    def stop(self) -> None:
        self._stopped.set()

    async def run_once(self) -> None:
        now = datetime.now(ZoneInfo(self.timezone))
        blacklist = Blacklist.from_entries(self.db.get_blacklist_values())
        campaigns = self.db.list_campaigns(CampaignStatus.RUNNING)
        for campaign in campaigns:
            if not ScheduleWindow.parse(campaign.schedule_window).contains(now):
                continue
            accounts = [account for account in self.db.list_accounts() if account.enabled]
            if campaign.source_account_username:
                source_username = normalize_username(campaign.source_account_username)
                accounts = [
                    account
                    for account in accounts
                    if normalize_username(account.username) == source_username
                ]
            chat_steps = self.db.get_sequence_steps(campaign.chat_sequence_id or campaign.sequence_id)
            private_steps = self.db.get_sequence_steps(campaign.private_sequence_id or campaign.sequence_id)
            if not accounts:
                continue
            for account in accounts:
                client = await self.accounts.client_for(account.session_path)
                try:
                    delivery = DeliveryClient(client)
                    chats = await delivery.scan_group_chats(campaign.source_folder)
                    chat_recipients = [
                        Recipient(
                            id=chat.id,
                            kind="chat",
                            username=chat.username,
                            days_since_last_message=chat.days_since_last_message,
                        )
                        for chat in chats
                    ]
                    selected_chats = select_recipients(
                        chat_recipients,
                        mode=ActivityMode(campaign.activity_mode),
                        days_threshold=campaign.days_threshold,
                        include_chats=True,
                        include_private=False,
                        blacklist=blacklist,
                    )
                    for chat in selected_chats:
                        non_team_messages = await delivery.count_recent_non_team_messages(
                            chat.id,
                            days=campaign.segment_window_days or 30,
                            team_identifiers=campaign.team_identifiers or [],
                        )
                        if campaign.target_segment == "warm" and non_team_messages <= campaign.segment_min_non_team_messages:
                            continue
                        if campaign.target_segment == "cold" and non_team_messages > campaign.segment_min_non_team_messages:
                            continue
                        if self.db.has_delivery_log(
                            campaign_id=campaign.id,
                            recipient_id=chat.id,
                            recipient_kind="chat",
                        ):
                            continue
                        if self.db.has_delivery_for_key(
                            dedupe_key=campaign.dedupe_key,
                            recipient_id=chat.id,
                            recipient_kind="chat",
                        ):
                            continue
                        private_recipients = []
                        if campaign.include_private and private_steps:
                            async for participant in delivery.iter_chat_participants(chat.id):
                                if blacklist.matches(participant):
                                    continue
                                if self._is_team_recipient(participant, campaign.team_identifiers or []):
                                    continue
                                if self.db.has_delivery_log(
                                    campaign_id=campaign.id,
                                    recipient_id=participant.id,
                                    recipient_kind="private",
                                ):
                                    continue
                                if self.db.has_delivery_for_key(
                                    dedupe_key=campaign.dedupe_key,
                                    recipient_id=participant.id,
                                    recipient_kind="private",
                                ):
                                    continue
                                private_recipients.append(participant)
                        mentions_text = self._mentions_text(private_recipients)
                        if campaign.include_chats and chat_steps:
                            await self._send_steps(
                                delivery,
                                campaign_id=campaign.id,
                                account_telegram_id=account.telegram_id,
                                dedupe_key=campaign.dedupe_key,
                                recipient=chat,
                                steps=chat_steps,
                                detail_prefix=f"chat; non_team_30d={non_team_messages}",
                                append_to_first_text=mentions_text,
                            )
                            await asyncio.sleep(campaign.delay_between_recipients_seconds)
                        if campaign.include_private and private_steps:
                            for participant in private_recipients:
                                await self._send_steps(
                                    delivery,
                                    campaign_id=campaign.id,
                                    account_telegram_id=account.telegram_id,
                                    dedupe_key=campaign.dedupe_key,
                                    recipient=participant,
                                    steps=private_steps,
                                    detail_prefix=f"participant of chat {chat.id}; non_team_30d={non_team_messages}",
                                )
                                await asyncio.sleep(campaign.delay_between_recipients_seconds)
                finally:
                    await client.disconnect()

    async def _send_steps(
        self,
        delivery: DeliveryClient,
        *,
        campaign_id: int,
        account_telegram_id: int,
        dedupe_key: str | None,
        recipient: Recipient,
        steps,
        detail_prefix: str,
        append_to_first_text: str = "",
    ) -> None:
        try:
            appended = False
            for step in steps:
                payload = step.payload
                if append_to_first_text and not appended and payload.get("text"):
                    payload = dict(payload)
                    payload["text"] = f"{payload['text']}\n\n{append_to_first_text}"
                    appended = True
                await delivery.send_payload(recipient.id, payload)
                if step.delay_after_seconds:
                    await asyncio.sleep(step.delay_after_seconds)
            self.db.create_delivery_log(
                campaign_id=campaign_id,
                account_telegram_id=account_telegram_id,
                recipient_id=recipient.id,
                recipient_kind=recipient.kind,
                status="sent",
                detail=detail_prefix,
                dedupe_key=dedupe_key,
            )
        except Exception as exc:
            self.db.create_delivery_log(
                campaign_id=campaign_id,
                account_telegram_id=account_telegram_id,
                recipient_id=recipient.id,
                recipient_kind=recipient.kind,
                status="error",
                detail=f"{detail_prefix}: {str(exc)[:450]}",
                dedupe_key=dedupe_key,
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
