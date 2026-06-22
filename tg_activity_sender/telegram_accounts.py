from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import qrcode
from telethon import TelegramClient, functions, utils
from telethon.errors import SessionPasswordNeededError
from telethon.tl.custom import Dialog

from tg_activity_sender.core import Recipient
from tg_activity_sender.db import Database


@dataclass
class QrLoginTicket:
    token: str
    url: str
    qr_png_path: Path


@dataclass(frozen=True)
class ChatTarget:
    id: int
    title: str
    username: str | None
    days_since_last_message: int


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

    async def list_folders(self) -> list[tuple[str, str]]:
        try:
            filters = await self.client(functions.messages.GetDialogFiltersRequest())
        except Exception:
            return []
        result: list[tuple[str, str]] = []
        for item in filters:
            folder_id = getattr(item, "id", None)
            title = getattr(item, "title", None)
            if folder_id is None or not title:
                continue
            result.append((str(folder_id), str(title)))
        return result

    async def scan_group_chats(self, source_folder: str | None = None) -> list[ChatTarget]:
        folder = source_folder.strip().lower() if source_folder else None
        folder_peer_ids = await self._folder_peer_ids(folder) if folder else None
        chats: list[ChatTarget] = []
        async for dialog in self.client.iter_dialogs():
            if not _dialog_kind(dialog) == "chat":
                continue
            if folder_peer_ids is not None and dialog.id not in folder_peer_ids:
                continue
            top_message = getattr(dialog, "message", None)
            if not top_message or not getattr(top_message, "date", None):
                continue
            days = max((datetime.now(top_message.date.tzinfo) - top_message.date).days, 0)
            chats.append(
                ChatTarget(
                    id=dialog.id,
                    title=getattr(dialog, "title", "") or str(dialog.id),
                    username=getattr(dialog.entity, "username", None),
                    days_since_last_message=days,
                )
            )
        return chats

    async def iter_chat_participants(self, chat_id: int):
        async for user in self.client.iter_participants(chat_id):
            if getattr(user, "bot", False) or getattr(user, "deleted", False):
                continue
            yield Recipient(
                id=user.id,
                kind="private",
                username=getattr(user, "username", None),
                days_since_last_message=0,
            )

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

    async def _folder_peer_ids(self, folder: str) -> set[int] | None:
        try:
            filters = await self.client(functions.messages.GetDialogFiltersRequest())
        except Exception:
            return None
        matched_filter = None
        for item in filters:
            folder_id = str(getattr(item, "id", "")).lower()
            title = str(getattr(item, "title", "")).lower()
            if folder in {folder_id, title}:
                matched_filter = item
                break
        if matched_filter is None:
            return None
        peer_ids: set[int] = set()
        for peer in getattr(matched_filter, "include_peers", []) or []:
            try:
                entity = await self.client.get_entity(peer)
                peer_ids.add(utils.get_peer_id(entity))
            except Exception:
                continue
        return peer_ids


def _dialog_kind(dialog: Dialog) -> str:
    if getattr(dialog, "is_user", False):
        return "private"
    entity = getattr(dialog, "entity", None)
    if getattr(entity, "broadcast", False):
        return "channel"
    return "chat"
