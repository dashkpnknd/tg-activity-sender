from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Iterable
import re
import unicodedata


class ActivityMode(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BOTH = "both"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FINISHED = "finished"


@dataclass(frozen=True)
class ScheduleWindow:
    start: time
    end: time

    @classmethod
    def parse(cls, value: str) -> "ScheduleWindow":
        try:
            start_raw, end_raw = value.split("-", 1)
            start = time.fromisoformat(start_raw.strip())
            end = time.fromisoformat(end_raw.strip())
        except ValueError as exc:
            raise ValueError("Schedule window must use HH:MM-HH:MM format") from exc
        return cls(start=start, end=end)

    def contains(self, moment: datetime) -> bool:
        current = moment.time().replace(second=0, microsecond=0)
        if self.start <= self.end:
            return self.start <= current <= self.end
        return current >= self.start or current <= self.end


@dataclass(frozen=True)
class Recipient:
    id: int
    kind: str
    username: str | None
    days_since_last_message: int


def is_avito_chat_title(title: str) -> bool:
    """Return whether a chat title explicitly identifies the Avito channel."""
    return bool(re.search(r"(?<![a-zа-я])авито|(?<![a-zа-я])avito", title.casefold()))


def project_key_from_chat_title(title: str) -> str:
    """Extract a stable project key from a Telegram chat title."""
    first_segment = title.split("|", 1)[0]
    value = unicodedata.normalize("NFKC", first_segment).casefold()
    value = re.sub(r"\b(?:авито|avito|вк|vk|target|таргет)\b", " ", value)
    value = re.sub(r"[^a-zа-я0-9]+", " ", value).strip()
    return re.sub(r"\s+", " ", value)


@dataclass(frozen=True)
class Blacklist:
    ids: frozenset[int]
    usernames: frozenset[str]

    @classmethod
    def from_entries(cls, entries: Iterable[str | int]) -> "Blacklist":
        ids: set[int] = set()
        usernames: set[str] = set()
        for entry in entries:
            raw = str(entry).strip()
            if not raw:
                continue
            if raw.lstrip("-").isdigit():
                ids.add(int(raw))
                continue
            username = raw.rstrip("/").split("/")[-1].removeprefix("@").lower()
            if username:
                usernames.add(username)
        return cls(ids=frozenset(ids), usernames=frozenset(usernames))

    def matches(self, recipient: Recipient) -> bool:
        if recipient.id in self.ids:
            return True
        if recipient.username and recipient.username.lower().removeprefix("@") in self.usernames:
            return True
        return False


def select_recipients(
    recipients: Iterable[Recipient],
    *,
    mode: ActivityMode,
    days_threshold: int,
    include_chats: bool,
    include_private: bool,
    blacklist: Blacklist,
) -> list[Recipient]:
    selected: list[Recipient] = []
    for recipient in recipients:
        if recipient.kind == "chat" and not include_chats:
            continue
        if recipient.kind == "private" and not include_private:
            continue
        if blacklist.matches(recipient):
            continue
        if mode == ActivityMode.ACTIVE and recipient.days_since_last_message > days_threshold:
            continue
        if mode == ActivityMode.INACTIVE and recipient.days_since_last_message <= days_threshold:
            continue
        selected.append(recipient)
    return selected


def can_transition(current: CampaignStatus, target: CampaignStatus) -> bool:
    allowed = {
        CampaignStatus.DRAFT: {CampaignStatus.RUNNING, CampaignStatus.STOPPED},
        CampaignStatus.RUNNING: {
            CampaignStatus.PAUSED,
            CampaignStatus.STOPPED,
            CampaignStatus.FINISHED,
        },
        CampaignStatus.PAUSED: {CampaignStatus.RUNNING, CampaignStatus.STOPPED},
        CampaignStatus.STOPPED: set(),
        CampaignStatus.FINISHED: set(),
    }
    return target in allowed[current]
