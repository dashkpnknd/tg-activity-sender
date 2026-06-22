from datetime import datetime

import pytest

from tg_activity_sender.core import (
    ActivityMode,
    Blacklist,
    CampaignStatus,
    Recipient,
    ScheduleWindow,
    can_transition,
    select_recipients,
)


def test_schedule_window_matches_hours_inside_same_day():
    window = ScheduleWindow.parse("10:00-19:30")

    assert window.contains(datetime(2026, 6, 22, 10, 0))
    assert window.contains(datetime(2026, 6, 22, 14, 15))
    assert not window.contains(datetime(2026, 6, 22, 9, 59))
    assert not window.contains(datetime(2026, 6, 22, 19, 31))


def test_schedule_window_matches_overnight_range():
    window = ScheduleWindow.parse("22:00-02:00")

    assert window.contains(datetime(2026, 6, 22, 23, 0))
    assert window.contains(datetime(2026, 6, 23, 1, 30))
    assert not window.contains(datetime(2026, 6, 22, 15, 0))


def test_blacklist_matches_id_username_and_chat_link():
    blacklist = Blacklist.from_entries(["123", "@BlockedUser", "https://t.me/blocked_chat"])

    assert blacklist.matches(Recipient(id=123, kind="private", username="any", days_since_last_message=1))
    assert blacklist.matches(Recipient(id=456, kind="private", username="blockeduser", days_since_last_message=1))
    assert blacklist.matches(Recipient(id=-100, kind="chat", username="blocked_chat", days_since_last_message=1))
    assert not blacklist.matches(Recipient(id=789, kind="chat", username="allowed", days_since_last_message=1))


def test_select_recipients_filters_by_activity_target_and_blacklist():
    recipients = [
        Recipient(id=1, kind="chat", username="active_chat", days_since_last_message=2),
        Recipient(id=2, kind="chat", username="old_chat", days_since_last_message=12),
        Recipient(id=3, kind="private", username="person", days_since_last_message=1),
        Recipient(id=4, kind="private", username="blocked", days_since_last_message=20),
    ]
    blacklist = Blacklist.from_entries(["blocked"])

    selected = select_recipients(
        recipients,
        mode=ActivityMode.INACTIVE,
        days_threshold=10,
        include_chats=True,
        include_private=False,
        blacklist=blacklist,
    )

    assert [recipient.id for recipient in selected] == [2]


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (CampaignStatus.DRAFT, CampaignStatus.RUNNING, True),
        (CampaignStatus.RUNNING, CampaignStatus.PAUSED, True),
        (CampaignStatus.PAUSED, CampaignStatus.RUNNING, True),
        (CampaignStatus.RUNNING, CampaignStatus.STOPPED, True),
        (CampaignStatus.STOPPED, CampaignStatus.RUNNING, False),
        (CampaignStatus.FINISHED, CampaignStatus.RUNNING, False),
    ],
)
def test_campaign_status_transitions(current, target, allowed):
    assert can_transition(current, target) is allowed

