from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from tg_activity_sender.core import ActivityMode, Blacklist, CampaignStatus, ScheduleWindow, select_recipients
from tg_activity_sender.db import Database
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
        accounts = [account for account in self.db.list_accounts() if account.enabled]
        for campaign in campaigns:
            if not ScheduleWindow.parse(campaign.schedule_window).contains(now):
                continue
            steps = self.db.get_sequence_steps(campaign.sequence_id)
            if not steps or not accounts:
                continue
            for account in accounts:
                client = await self.accounts.client_for(account.session_path)
                try:
                    delivery = DeliveryClient(client)
                    recipients = await delivery.scan_recipients()
                    selected = select_recipients(
                        recipients,
                        mode=ActivityMode(campaign.activity_mode),
                        days_threshold=campaign.days_threshold,
                        include_chats=campaign.include_chats,
                        include_private=campaign.include_private,
                        blacklist=blacklist,
                    )
                    for recipient in selected:
                        try:
                            for step in steps:
                                await delivery.send_payload(recipient.id, step.payload)
                                if step.delay_after_seconds:
                                    await asyncio.sleep(step.delay_after_seconds)
                            self.db.create_delivery_log(
                                campaign_id=campaign.id,
                                account_telegram_id=account.telegram_id,
                                recipient_id=recipient.id,
                                recipient_kind=recipient.kind,
                                status="sent",
                                detail="ok",
                            )
                        except Exception as exc:
                            self.db.create_delivery_log(
                                campaign_id=campaign.id,
                                account_telegram_id=account.telegram_id,
                                recipient_id=recipient.id,
                                recipient_kind=recipient.kind,
                                status="error",
                                detail=str(exc)[:500],
                            )
                        await asyncio.sleep(campaign.delay_between_recipients_seconds)
                finally:
                    await client.disconnect()

