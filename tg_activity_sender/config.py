from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    telegram_api_id: int
    telegram_api_hash: str
    admin_ids: frozenset[int]
    database_path: Path
    media_dir: Path
    session_dir: Path
    telegram_proxy_url: str | None
    timezone: str
    default_schedule_window: str
    default_delay_seconds: int
    stop_words: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        admin_ids = frozenset(
            int(item.strip())
            for item in os.getenv("ADMIN_IDS", "").split(",")
            if item.strip()
        )
        return cls(
            bot_token=_required("BOT_TOKEN"),
            telegram_api_id=int(_required("TELEGRAM_API_ID")),
            telegram_api_hash=_required("TELEGRAM_API_HASH"),
            admin_ids=admin_ids,
            database_path=Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")),
            media_dir=Path(os.getenv("MEDIA_DIR", "data/media")),
            session_dir=Path(os.getenv("SESSION_DIR", "sessions")),
            telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL") or None,
            timezone=os.getenv("TIMEZONE", "Europe/Moscow"),
            default_schedule_window=os.getenv("DEFAULT_SCHEDULE_WINDOW", "10:00-20:00"),
            default_delay_seconds=int(os.getenv("DEFAULT_DELAY_SECONDS", "300")),
            stop_words=tuple(
                item.strip().lower()
                for item in os.getenv("STOP_WORDS", "стоп,не писать,отписаться,stop,unsubscribe").split(",")
                if item.strip()
            ),
        )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value
