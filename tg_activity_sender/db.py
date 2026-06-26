from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from tg_activity_sender.core import ActivityMode, CampaignStatus


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    session_path: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MessageSequence(Base):
    __tablename__ = "message_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    steps: Mapped[list["SequenceStep"]] = relationship(
        back_populates="sequence",
        cascade="all, delete-orphan",
        order_by="SequenceStep.order",
    )


class SequenceStep(Base):
    __tablename__ = "sequence_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("message_sequences.id", ondelete="CASCADE"))
    order: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    delay_after_seconds: Mapped[int] = mapped_column(Integer, default=0)
    sequence: Mapped[MessageSequence] = relationship(back_populates="steps")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("message_sequences.id"))
    chat_sequence_id: Mapped[int | None] = mapped_column(ForeignKey("message_sequences.id"), nullable=True)
    private_sequence_id: Mapped[int | None] = mapped_column(ForeignKey("message_sequences.id"), nullable=True)
    source_folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_account_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_segment: Mapped[str] = mapped_column(String(32), default="all")
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_identifiers: Mapped[list[str]] = mapped_column(JSON, default=list)
    segment_window_days: Mapped[int] = mapped_column(Integer, default=30)
    segment_min_non_team_messages: Mapped[int] = mapped_column(Integer, default=5)
    activity_mode: Mapped[ActivityMode] = mapped_column(String(32))
    days_threshold: Mapped[int] = mapped_column(Integer)
    include_chats: Mapped[bool] = mapped_column(Boolean, default=True)
    include_private: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_window: Mapped[str] = mapped_column(String(32))
    delay_between_recipients_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[CampaignStatus] = mapped_column(String(32), default=CampaignStatus.DRAFT)


class BlacklistEntry(Base):
    __tablename__ = "blacklist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    normalized_value: Mapped[str] = mapped_column(String(255), unique=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    account_telegram_id: Mapped[int] = mapped_column(Integer)
    recipient_id: Mapped[int] = mapped_column(Integer)
    recipient_kind: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")


def normalize_blacklist_value(value: str | int) -> str:
    raw = str(value).strip()
    if raw.lstrip("-").isdigit():
        return raw
    return raw.rstrip("/").split("/")[-1].removeprefix("@").lower()


def normalize_blacklist_values(value: str | int) -> list[str]:
    raw = str(value).strip()
    if not raw:
        return []
    values = []
    for item in re.split(r"[\s,;]+", raw):
        normalized = normalize_blacklist_value(item)
        if normalized:
            values.append(normalized)
    return values


def normalize_username(value: str | None) -> str:
    return (value or "").strip().rstrip("/").split("/")[-1].removeprefix("@").lower()


def normalize_key(value: str | None) -> str | None:
    normalized = re.sub(r"[^a-z0-9а-я_-]+", "-", (value or "").strip().lower()).strip("-")
    return normalized or None


def display_blacklist_value(value: str) -> str:
    normalized = normalize_blacklist_value(value)
    if not normalized:
        return value
    if normalized.lstrip("-").isdigit():
        return normalized
    return f"@{normalized}"


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.engine = create_engine(f"sqlite:///{self.path}", future=True)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> None:
        inspector = inspect(self.engine)
        if not inspector.has_table("campaigns"):
            return
        columns = {column["name"] for column in inspector.get_columns("campaigns")}
        statements = []
        if "chat_sequence_id" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN chat_sequence_id INTEGER")
        if "private_sequence_id" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN private_sequence_id INTEGER")
        if "source_folder" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN source_folder VARCHAR(255)")
        if "source_account_username" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN source_account_username VARCHAR(255)")
        if "target_segment" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN target_segment VARCHAR(32) DEFAULT 'all'")
        if "dedupe_key" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN dedupe_key VARCHAR(255)")
        if "team_identifiers" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN team_identifiers JSON DEFAULT '[]'")
        if "segment_window_days" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN segment_window_days INTEGER DEFAULT 30")
        if "segment_min_non_team_messages" not in columns:
            statements.append("ALTER TABLE campaigns ADD COLUMN segment_min_non_team_messages INTEGER DEFAULT 5")
        log_columns = set()
        if inspector.has_table("delivery_logs"):
            log_columns = {column["name"] for column in inspector.get_columns("delivery_logs")}
        if inspector.has_table("delivery_logs") and "dedupe_key" not in log_columns:
            statements.append("ALTER TABLE delivery_logs ADD COLUMN dedupe_key VARCHAR(255)")
        with self.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    def session(self) -> Session:
        return self.session_factory()

    def create_account(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str,
        session_path: str,
    ) -> Account:
        with self.session() as session:
            account = Account(
                telegram_id=telegram_id,
                username=username,
                display_name=display_name,
                session_path=session_path,
                enabled=True,
            )
            session.add(account)
            session.commit()
            return account

    def list_accounts(self) -> list[Account]:
        with self.session() as session:
            return list(session.scalars(select(Account).order_by(Account.id)))

    def find_account_by_username(self, username: str) -> Account | None:
        normalized = normalize_username(username)
        with self.session() as session:
            for account in session.scalars(select(Account)):
                if normalize_username(account.username) == normalized:
                    return account
            return None

    def create_sequence(self, name: str) -> MessageSequence:
        with self.session() as session:
            sequence = MessageSequence(name=name)
            session.add(sequence)
            session.commit()
            return sequence

    def find_sequence_by_name(self, name: str) -> MessageSequence | None:
        with self.session() as session:
            return session.scalar(select(MessageSequence).where(MessageSequence.name == name))

    def add_sequence_step(
        self,
        sequence_id: int,
        *,
        order: int,
        payload: dict[str, Any],
        delay_after_seconds: int,
    ) -> SequenceStep:
        with self.session() as session:
            step = SequenceStep(
                sequence_id=sequence_id,
                order=order,
                payload=payload,
                delay_after_seconds=delay_after_seconds,
            )
            session.add(step)
            session.commit()
            return step

    def get_sequence_steps(self, sequence_id: int) -> list[SequenceStep]:
        with self.session() as session:
            stmt = select(SequenceStep).where(SequenceStep.sequence_id == sequence_id).order_by(SequenceStep.order)
            return list(session.scalars(stmt))

    def create_campaign(
        self,
        *,
        name: str,
        sequence_id: int,
        activity_mode: ActivityMode,
        days_threshold: int,
        include_chats: bool,
        include_private: bool,
        schedule_window: str,
        delay_between_recipients_seconds: int,
        chat_sequence_id: int | None = None,
        private_sequence_id: int | None = None,
        source_folder: str | None = None,
        source_account_username: str | None = None,
        target_segment: str = "all",
        dedupe_key: str | None = None,
        team_identifiers: list[str] | None = None,
        segment_window_days: int = 30,
        segment_min_non_team_messages: int = 5,
    ) -> Campaign:
        with self.session() as session:
            campaign = Campaign(
                name=name,
                sequence_id=sequence_id,
                chat_sequence_id=chat_sequence_id or sequence_id,
                private_sequence_id=private_sequence_id or sequence_id,
                source_folder=source_folder,
                source_account_username=normalize_username(source_account_username) if source_account_username else None,
                target_segment=target_segment,
                dedupe_key=normalize_key(dedupe_key),
                team_identifiers=team_identifiers or [],
                segment_window_days=segment_window_days,
                segment_min_non_team_messages=segment_min_non_team_messages,
                activity_mode=activity_mode,
                days_threshold=days_threshold,
                include_chats=include_chats,
                include_private=include_private,
                schedule_window=schedule_window,
                delay_between_recipients_seconds=delay_between_recipients_seconds,
                status=CampaignStatus.DRAFT,
            )
            session.add(campaign)
            session.commit()
            return campaign

    def add_blacklist_entry(self, *, value: str | int, reason: str = "") -> BlacklistEntry:
        normalized_values = normalize_blacklist_values(value)
        if len(normalized_values) > 1:
            first_entry = None
            for item in normalized_values:
                entry = self.add_blacklist_entry(value=item, reason=reason)
                if first_entry is None:
                    first_entry = entry
            if first_entry is not None:
                return first_entry
        with self.session() as session:
            normalized_value = normalize_blacklist_value(value)
            entry = BlacklistEntry(
                value=str(value).strip(),
                normalized_value=normalized_value,
                reason=reason,
            )
            session.add(entry)
            try:
                session.commit()
                return entry
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(BlacklistEntry).where(BlacklistEntry.normalized_value == normalized_value)
                )
                if existing is None:
                    raise
                return existing

    def list_blacklist_entries(self) -> list[BlacklistEntry]:
        with self.session() as session:
            return list(session.scalars(select(BlacklistEntry).order_by(BlacklistEntry.id)))

    def create_delivery_log(
        self,
        *,
        campaign_id: int,
        account_telegram_id: int,
        recipient_id: int,
        recipient_kind: str,
        status: str,
        detail: str = "",
        dedupe_key: str | None = None,
    ) -> DeliveryLog:
        with self.session() as session:
            log = DeliveryLog(
                campaign_id=campaign_id,
                account_telegram_id=account_telegram_id,
                recipient_id=recipient_id,
                recipient_kind=recipient_kind,
                dedupe_key=normalize_key(dedupe_key),
                status=status,
                detail=detail,
            )
            session.add(log)
            session.commit()
            return log

    def list_delivery_logs(self, campaign_id: int) -> list[DeliveryLog]:
        with self.session() as session:
            stmt = select(DeliveryLog).where(DeliveryLog.campaign_id == campaign_id).order_by(DeliveryLog.id)
            return list(session.scalars(stmt))

    def list_sequences(self) -> list[MessageSequence]:
        with self.session() as session:
            return list(session.scalars(select(MessageSequence).order_by(MessageSequence.id)))

    def list_campaigns(self, status: CampaignStatus | None = None) -> list[Campaign]:
        with self.session() as session:
            stmt = select(Campaign).order_by(Campaign.id)
            if status is not None:
                stmt = stmt.where(Campaign.status == status)
            return list(session.scalars(stmt))

    def get_campaign(self, campaign_id: int) -> Campaign | None:
        with self.session() as session:
            return session.get(Campaign, campaign_id)

    def update_campaign_status(self, campaign_id: int, status: CampaignStatus) -> None:
        with self.session() as session:
            session.execute(update(Campaign).where(Campaign.id == campaign_id).values(status=status))
            session.commit()

    def get_blacklist_values(self) -> list[str]:
        values = []
        seen = set()
        for entry in self.list_blacklist_entries():
            for normalized in normalize_blacklist_values(entry.normalized_value):
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                values.append(normalized)
        return values

    def recent_delivery_logs(self, limit: int = 20) -> list[DeliveryLog]:
        with self.session() as session:
            stmt = select(DeliveryLog).order_by(DeliveryLog.id.desc()).limit(limit)
            return list(session.scalars(stmt))

    def has_delivery_log(self, *, campaign_id: int, recipient_id: int, recipient_kind: str, status: str = "sent") -> bool:
        with self.session() as session:
            stmt = (
                select(DeliveryLog.id)
                .where(DeliveryLog.campaign_id == campaign_id)
                .where(DeliveryLog.recipient_id == recipient_id)
                .where(DeliveryLog.recipient_kind == recipient_kind)
                .where(DeliveryLog.status == status)
                .limit(1)
            )
            return session.scalar(stmt) is not None

    def has_delivery_for_key(
        self,
        *,
        dedupe_key: str | None,
        recipient_id: int,
        recipient_kind: str,
        status: str = "sent",
    ) -> bool:
        normalized_key = normalize_key(dedupe_key)
        if not normalized_key:
            return False
        with self.session() as session:
            stmt = (
                select(DeliveryLog.id)
                .where(DeliveryLog.dedupe_key == normalized_key)
                .where(DeliveryLog.recipient_id == recipient_id)
                .where(DeliveryLog.recipient_kind == recipient_kind)
                .where(DeliveryLog.status == status)
                .limit(1)
            )
            return session.scalar(stmt) is not None
