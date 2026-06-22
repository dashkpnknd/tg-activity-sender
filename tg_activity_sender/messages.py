from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_MEDIA_KEYS = {
    "photo",
    "video",
    "audio",
    "document",
    "voice",
    "video_note",
    "sticker",
    "animation",
}


@dataclass(frozen=True)
class StoredMessage:
    payload: dict[str, Any]

    def validate(self) -> None:
        if not self.payload:
            raise ValueError("Message payload cannot be empty")
        allowed = SUPPORTED_MEDIA_KEYS | {"text"}
        unknown = set(self.payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported message payload keys: {', '.join(sorted(unknown))}")

    @property
    def has_media(self) -> bool:
        return any(key in self.payload for key in SUPPORTED_MEDIA_KEYS)


@dataclass(frozen=True)
class MessageStep:
    order: int
    message: StoredMessage
    delay_after_seconds: int = 0

    def validate(self) -> None:
        if self.order < 1:
            raise ValueError("Step order starts from 1")
        if self.delay_after_seconds < 0:
            raise ValueError("Step delay cannot be negative")
        self.message.validate()


def sequence_duration_seconds(steps: list[MessageStep]) -> int:
    for step in steps:
        step.validate()
    return sum(step.delay_after_seconds for step in steps)

