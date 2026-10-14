"""SRA-502 — Digest scheduler: users_due_for_wakeup, run_wakeup_tick."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

import logging
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from sra.db.models import ReviewSchedule, StudyEntry, StudyEntryCreate, User

pytestmark = pytest.mark.phase5


@pytest.fixture
def digest_scheduler():
    from sra.jobs import digest_scheduler as _ds

    return _ds


def _make_user(session, *, email: str, google_sub: str, review_time: str = "09:00", tz: str = "UTC") -> User:
    user = User(
        email=email,
        display_name=email,
        google_sub=google_sub,
        preferred_review_time=review_time,
        timezone=tz,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    return user


def _make_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _add_pending_review(session, entry_id: int) -> ReviewSchedule:
    review = ReviewSchedule(
        entry_id=entry_id, interval_label="1d", scheduled_date=datetime.now(UTC).date(), status="pending"
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def test_users_due_for_wakeup_returns_user_just_after_their_local_review_time(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of, session=session)

    assert [u.id for u in due] == [user.id]


def test_users_due_for_wakeup_excludes_user_already_notified_today(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id)
    user.wakeup_notification_sent_at = datetime(2026, 10, 14, 9, 5, tzinfo=UTC)
    session.add(user)
    session.commit()
    session.refresh(user)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of, session=session)

    assert due == []


def test_users_due_for_wakeup_includes_user_again_next_local_day(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id)
    user.wakeup_notification_sent_at = datetime(2026, 10, 13, 9, 5, tzinfo=UTC)
    session.add(user)
    session.commit()
    session.refresh(user)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of, session=session)

    assert [u.id for u in due] == [user.id]


def test_users_due_for_wakeup_converts_to_local_timezone_before_comparing(session, digest_scheduler):
    """An America/Los_Angeles user whose local review time is 07:30 must be due at
    UTC 14:30 (PDT = UTC-7 in October)."""
    user = _make_user(
        session,
        email="la@example.com",
        google_sub="la-sub",
        review_time="07:30",
        tz="America/Los_Angeles",
    )
    _add_pending_review(session, _make_entry(session, user.id).id)

    as_of_just_before = datetime(2026, 10, 14, 14, 29, tzinfo=UTC)
    assert digest_scheduler.users_due_for_wakeup(as_of_utc=as_of_just_before, session=session) == []

    as_of_just_after = datetime(2026, 10, 14, 14, 31, tzinfo=UTC)
    due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of_just_after, session=session)
    assert [u.id for u in due] == [user.id]


def test_users_due_for_wakeup_skips_and_logs_bad_timezone_user(session, caplog, digest_scheduler):
    good = _make_user(session, email="good@example.com", google_sub="good-sub")
    _add_pending_review(session, _make_entry(session, good.id).id)
    bad = _make_user(session, email="bad@example.com", google_sub="bad-sub")
    _add_pending_review(session, _make_entry(session, bad.id).id)
    bad.timezone = "Mars/Olympus"
    session.add(bad)
    session.commit()
    session.refresh(bad)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    with caplog.at_level(logging.WARNING):
        due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of, session=session)

    assert [u.id for u in due] == [good.id]
    assert any("Mars/Olympus" in record.getMessage() for record in caplog.records)


def test_users_due_for_wakeup_excludes_users_without_pending_reviews_today(session, digest_scheduler):
    """A user whose only reviews are completed is not 'due' for a wakeup."""
    user = _make_user(session, email="a@example.com", google_sub="a-sub")
    entry = _make_entry(session, user.id)
    done = ReviewSchedule(
        entry_id=entry.id,
        interval_label="1d",
        scheduled_date=datetime.now(UTC).date(),
        status="done",
    )
    session.add(done)
    session.commit()

    due = digest_scheduler.users_due_for_wakeup(
        as_of_utc=datetime(2026, 10, 14, 9, 30, tzinfo=UTC), session=session
    )
    assert due == []


def test_run_wakeup_tick_sets_wakeup_notification_sent_at_for_due_users(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id)
    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)

    count = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)

    assert count == 1
    session.refresh(user)
    assert user.wakeup_notification_sent_at == as_of


def test_run_wakeup_tick_is_idempotent_within_a_local_day(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id)
    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)

    first = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)
    second = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)

    assert first == 1
    assert second == 0


def test_run_wakeup_tick_continues_past_a_per_user_exception(session, monkeypatch, caplog, digest_scheduler):
    good = _make_user(session, email="good@example.com", google_sub="good-sub")
    _add_pending_review(session, _make_entry(session, good.id).id)
    bad = _make_user(session, email="bad@example.com", google_sub="bad-sub", tz="Mars/Olympus")
    _add_pending_review(session, _make_entry(session, bad.id).id)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    with caplog.at_level(logging.WARNING):
        count = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)

    assert count == 1
    session.refresh(good)
    assert good.wakeup_notification_sent_at == as_of


def test_run_wakeup_tick_includes_the_next_day(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id)

    first_day = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    assert digest_scheduler.run_wakeup_tick(session=session, as_of_utc=first_day) == 1

    next_day = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
    assert digest_scheduler.run_wakeup_tick(session=session, as_of_utc=next_day) == 1

    refreshed = session.exec(select(User).where(User.id == user.id)).one()
    assert refreshed.wakeup_notification_sent_at == next_day
