from tg_activity_sender.core import ActivityMode, CampaignStatus
from tg_activity_sender.db import Database


def test_database_creates_account_and_keeps_metadata(tmp_path):
    db = Database(tmp_path / "bot.sqlite3")
    db.init()

    account = db.create_account(
        telegram_id=101,
        username="sender_one",
        display_name="Sender One",
        session_path="sessions/101.session",
    )

    assert account.telegram_id == 101
    assert account.username == "sender_one"
    assert account.enabled is True
    assert db.list_accounts()[0].session_path == "sessions/101.session"


def test_database_creates_sequence_with_ordered_steps(tmp_path):
    db = Database(tmp_path / "bot.sqlite3")
    db.init()

    sequence = db.create_sequence("Active chat follow-up")
    db.add_sequence_step(sequence.id, order=1, payload={"text": "hello"}, delay_after_seconds=5)
    db.add_sequence_step(
        sequence.id,
        order=2,
        payload={"video_note": [{"file_id": "abc", "file_name": "file.mp4"}]},
        delay_after_seconds=0,
    )

    steps = db.get_sequence_steps(sequence.id)
    assert [step.order for step in steps] == [1, 2]
    assert steps[1].payload["video_note"][0]["file_id"] == "abc"


def test_database_creates_campaign_with_rules(tmp_path):
    db = Database(tmp_path / "bot.sqlite3")
    db.init()
    sequence = db.create_sequence("Inactive private follow-up")

    campaign = db.create_campaign(
        name="June inactive",
        sequence_id=sequence.id,
        activity_mode=ActivityMode.INACTIVE,
        days_threshold=10,
        include_chats=True,
        include_private=True,
        schedule_window="10:00-20:00",
        delay_between_recipients_seconds=300,
    )

    assert campaign.status == CampaignStatus.DRAFT
    assert campaign.activity_mode == ActivityMode.INACTIVE
    assert campaign.schedule_window == "10:00-20:00"


def test_database_blacklist_and_delivery_logs(tmp_path):
    db = Database(tmp_path / "bot.sqlite3")
    db.init()
    sequence = db.create_sequence("Seq")
    campaign = db.create_campaign(
        name="Campaign",
        sequence_id=sequence.id,
        activity_mode=ActivityMode.ACTIVE,
        days_threshold=5,
        include_chats=False,
        include_private=True,
        schedule_window="09:00-18:00",
        delay_between_recipients_seconds=60,
    )

    entry = db.add_blacklist_entry(value="@blocked", reason="asked to stop")
    log = db.create_delivery_log(
        campaign_id=campaign.id,
        account_telegram_id=101,
        recipient_id=202,
        recipient_kind="private",
        status="sent",
        detail="ok",
    )

    assert entry.normalized_value == "blocked"
    assert db.list_blacklist_entries()[0].reason == "asked to stop"
    assert db.list_delivery_logs(campaign.id)[0].detail == "ok"
    assert log.status == "sent"

