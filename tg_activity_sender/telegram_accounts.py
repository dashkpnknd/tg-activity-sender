from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.custom import Dialog

from tg_activity_sender.core import Recipient
from tg_activity_sender.db import Database


@dataclass
class QrLoginTicket:
    token: str
    url: str
    qr_png_path: Path


class AccountManager:
    def __init__(self, db: Database, *, api_id: int, api_hash: str, session_dir: Path):
        self.db = db
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_dir = session_dir
        self._pending: dict[str, tuple[TelegramClient, Any, Path]] = {}

    async def begin_qr_login(self, token: str) -> QrLoginTicket:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        session_path = self.session_dir / f"pending_{token}"
        client = TelegramClient(str(session_path), self.api_id, self.api_hash)
        await client.connect()
        qr_login = await client.qr_login()
        qr_png_path = self.session_dir / f"qr_{token}.png"
        qrcode.make(qr_login.url).save(qr_png_path)
        self._pending[token] = (client, qr_login, session_path)
        return QrLoginTicket(token=token, url=qr_login.url, qr_png_path=qr_png_path)

    async def finish_qr_login(self, token: str, timeout_seconds: int = 120) -> int:
        client, qr_login, session_path = self._pending.pop(token)
        try:
            try:
                await asyncio.wait_for(qr_login.wait(), timeout=timeout_seconds)
            except SessionPasswordNeededError as exc:
                raise RuntimeError("На аккаунте включён 2FA-пароль; QR вошёл, но нужен пароль.") from exc
            me = await client.get_me()
            final_session = self.session_dir / f"{me.id}"
            await client.disconnect()
            if session_path.with_suffix(".session").exists():
                session_path.with_suffix(".session").rename(final_session.with_suffix(".session"))
            self.db.create_account(
                telegram_id=me.id,
                username=me.username,
                display_name=" ".join(part for part in [me.first_name, me.last_name] if part),
                session_path=str(final_session),
            )
            return me.id
        finally:
            if client.is_connected():
                await client.disconnect()

    async def client_for(self, session_path: str) -> TelegramClient:
        client = TelegramClient(session_path, self.api_id, self.api_hash)
        await client.start()
        return client


class DeliveryClient:
    def __init__(self, client: TelegramClient):
        self.client = client

    async def scan_recipients(self) -> list[Recipient]:
        recipients: list[Recipient] = []
        async for dialog in self.client.iter_dialogs():
            top_message = getattr(dialog, "message", None)
            if not top_message or not getattr(top_message, "date", None):
                continue
            kind = _dialog_kind(dialog)
            days = max((__import__("datetime").datetime.now(top_message.date.tzinfo) - top_message.date).days, 0)
            recipients.append(
                Recipient(
                    id=dialog.id,
                    kind=kind,
                    username=getattr(dialog.entity, "username", None),
                    days_since_last_message=days,
                )
            )
        return recipients

    async def send_payload(self, recipient_id: int, payload: dict[str, Any]) -> None:
        text = payload.get("text")
        if text and len(payload) == 1:
            await self.client.send_message(recipient_id, text, parse_mode="html")
            return
        for media_type in ("photo", "video", "audio", "document", "voice", "video_note", "sticker", "animation"):
            for item in payload.get(media_type, []):
                path = item.get("path")
                if not path:
                    continue
                if media_type == "video_note":
                    await self.client.send_file(recipient_id, path, video_note=True)
                elif media_type == "voice":
                    await self.client.send_file(recipient_id, path, voice_note=True, caption=text or None)
                else:
                    await self.client.send_file(recipient_id, path, caption=text or None)
                text = None
        if text:
            await self.client.send_message(recipient_id, text, parse_mode="html")


def _dialog_kind(dialog: Dialog) -> str:
    if getattr(dialog, "is_user", False):
        return "private"
    return "chat"

