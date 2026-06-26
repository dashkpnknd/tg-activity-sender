from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import qrcode
import socks
from telethon import TelegramClient, functions, utils
from telethon.errors import SessionPasswordNeededError
from telethon.tl.custom import Dialog

from tg_activity_sender.core import Recipient
from tg_activity_sender.db import Database, normalize_username


@dataclass
class QrLoginTicket:
    token: str
    url: str
    qr_png_path: Path


class TwoFactorPasswordRequired(Exception):
    pass


@dataclass(frozen=True)
class ChatTarget:
    id: int
    title: str
    username: str | None
    days_since_last_message: int


class AccountManager:
    def __init__(
        self,
        db: Database,
        *,
        api_id: int,
        api_hash: str,
        session_dir: Path,
        proxy_url: str | None = None,
    ):
        self.db = db
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_dir = session_dir
        self.proxy = parse_proxy_url(proxy_url)
        self._pending: dict[str, tuple[TelegramClient, Any, Path]] = {}

    async def begin_qr_login(self, token: str) -> QrLoginTicket:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        session_path = self.session_dir / f"pending_{token}"
        client = TelegramClient(str(session_path), self.api_id, self.api_hash, proxy=self.proxy)
        await asyncio.wait_for(client.connect(), timeout=30)
        qr_login = await asyncio.wait_for(client.qr_login(), timeout=30)
        qr_png_path = self.session_dir / f"qr_{token}.png"
        qrcode.make(qr_login.url).save(qr_png_path)
        self._pending[token] = (client, qr_login, session_path)
        return QrLoginTicket(token=token, url=qr_login.url, qr_png_path=qr_png_path)

    async def finish_qr_login(self, token: str, timeout_seconds: int = 120) -> int:
        client, qr_login, session_path = self._pending[token]
        try:
            await asyncio.wait_for(qr_login.wait(), timeout=timeout_seconds)
        except SessionPasswordNeededError as exc:
            raise TwoFactorPasswordRequired from exc
        except Exception:
            self._pending.pop(token, None)
            if client.is_connected():
                await client.disconnect()
            raise
        return await self._save_authorized_client(token, client, session_path)

    async def finish_2fa_login(self, token: str, password: str) -> int:
        client, _, session_path = self._pending[token]
        await asyncio.wait_for(client.sign_in(password=password), timeout=30)
        return await self._save_authorized_client(token, client, session_path)

    async def _save_authorized_client(self, token: str, client: TelegramClient, session_path: Path) -> int:
        try:
            me = await client.get_me()
            final_session = self.session_dir / f"{me.id}"
            if session_path.with_suffix(".session").exists():
                session_path.with_suffix(".session").rename(final_session.with_suffix(".session"))
            existing = self.db.find_account_by_username(me.username) if me.username else None
            if not existing:
                self.db.create_account(
                    telegram_id=me.id,
                    username=me.username,
                    display_name=" ".join(part for part in [me.first_name, me.last_name] if part),
                    session_path=str(final_session),
                )
            self._pending.pop(token, None)
            return me.id
        finally:
            if client.is_connected():
                await client.disconnect()

    async def client_for(self, session_path: str) -> TelegramClient:
        client = TelegramClient(session_path, self.api_id, self.api_hash, proxy=self.proxy)
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

    async def count_recent_non_team_messages(
        self,
        chat_id: int,
        *,
        days: int,
        team_identifiers: list[str],
    ) -> int:
        cutoff = datetime.now().astimezone() - timedelta(days=days)
        team = {normalize_username(item) for item in team_identifiers if item}
        count = 0
        async for message in self.client.iter_messages(chat_id, limit=500):
            if not message.date:
                continue
            message_date = message.date.astimezone()
            if message_date < cutoff:
                break
            if getattr(message, "out", False):
                continue
            sender = await message.get_sender()
            if sender is None:
                continue
            sender_identifiers = {
                str(getattr(sender, "id", "")),
                normalize_username(getattr(sender, "username", None)),
                normalize_username(getattr(sender, "first_name", None)),
                normalize_username(" ".join(
                    part
                    for part in [
                        getattr(sender, "first_name", None),
                        getattr(sender, "last_name", None),
                    ]
                    if part
                )),
            }
            if sender_identifiers & team:
                continue
            count += 1
        return count

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


def parse_proxy_url(proxy_url: str | None):
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    scheme_map = {
        "http": socks.HTTP,
        "socks4": socks.SOCKS4,
        "socks5": socks.SOCKS5,
    }
    proxy_type = scheme_map.get(parsed.scheme.lower())
    if proxy_type is None or not parsed.hostname or not parsed.port:
        raise ValueError("TELEGRAM_PROXY_URL must be http://user:pass@host:port, socks4://..., or socks5://...")
    return (
        proxy_type,
        parsed.hostname,
        parsed.port,
        True,
        parsed.username,
        parsed.password,
    )
