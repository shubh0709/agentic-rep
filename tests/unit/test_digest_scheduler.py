"""SRA-502 — Digest scheduler: users_due_for_wakeup, run_wakeup_tick."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

import logging
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
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


def _add_pending_review(
    session,
    entry_id: int,
    *,
    scheduled_date: date | None = None,
) -> ReviewSchedule:
    """Persist a pending ``ReviewSchedule`` whose ``scheduled_date`` is explicitly pinned.

    Tests freeze ``as_of_utc`` to a historical value (e.g. ``datetime(2026, 10, 14,
    9, 30, tzinfo=UTC)``). The production scheduler only treats a review as
    "due" when ``scheduled_date <= local_today`` for that ``as_of_utc``. Earlier
    revisions of this helper used ``datetime.now(UTC).date()`` so the test passed
    on the day the fixture landed and silently broke every day after; pass
    ``scheduled_date`` here so every test controls its own clock.
    """
    effective = scheduled_date if scheduled_date is not None else datetime.now(UTC).date()
    review = ReviewSchedule(
        entry_id=entry_id,
        interval_label="1d",
        scheduled_date=effective,
        status="pending",
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


_FIXED_AS_OF_DATE = date(2026, 10, 14)


def test_users_due_for_wakeup_returns_user_just_after_their_local_review_time(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id, scheduled_date=_FIXED_AS_OF_DATE)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of, session=session)

    assert [u.id for u in due] == [user.id]


def test_users_due_for_wakeup_excludes_user_already_notified_today(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id, scheduled_date=_FIXED_AS_OF_DATE)
    user.wakeup_notification_sent_at = datetime(2026, 10, 14, 9, 5, tzinfo=UTC)
    session.add(user)
    session.commit()
    session.refresh(user)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of, session=session)

    assert due == []


def test_users_due_for_wakeup_includes_user_again_next_local_day(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id, scheduled_date=_FIXED_AS_OF_DATE)
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
    _add_pending_review(session, _make_entry(session, user.id).id, scheduled_date=_FIXED_AS_OF_DATE)

    as_of_just_before = datetime(2026, 10, 14, 14, 29, tzinfo=UTC)
    assert digest_scheduler.users_due_for_wakeup(as_of_utc=as_of_just_before, session=session) == []

    as_of_just_after = datetime(2026, 10, 14, 14, 31, tzinfo=UTC)
    due = digest_scheduler.users_due_for_wakeup(as_of_utc=as_of_just_after, session=session)
    assert [u.id for u in due] == [user.id]


def test_users_due_for_wakeup_skips_and_logs_bad_timezone_user(session, caplog, digest_scheduler):
    good = _make_user(session, email="good@example.com", google_sub="good-sub")
    _add_pending_review(session, _make_entry(session, good.id).id, scheduled_date=_FIXED_AS_OF_DATE)
    bad = _make_user(session, email="bad@example.com", google_sub="bad-sub")
    _add_pending_review(session, _make_entry(session, bad.id).id, scheduled_date=_FIXED_AS_OF_DATE)
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
    _add_pending_review(session, _make_entry(session, user.id).id, scheduled_date=_FIXED_AS_OF_DATE)
    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)

    count = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)

    assert count == 1
    session.refresh(user)
    assert user.wakeup_notification_sent_at == as_of


def test_run_wakeup_tick_is_idempotent_within_a_local_day(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id, scheduled_date=_FIXED_AS_OF_DATE)
    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)

    first = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)
    second = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)

    assert first == 1
    assert second == 0


def test_run_wakeup_tick_continues_past_a_per_user_exception(session, monkeypatch, caplog, digest_scheduler):
    good = _make_user(session, email="good@example.com", google_sub="good-sub")
    _add_pending_review(session, _make_entry(session, good.id).id, scheduled_date=_FIXED_AS_OF_DATE)
    bad = _make_user(session, email="bad@example.com", google_sub="bad-sub", tz="Mars/Olympus")
    _add_pending_review(session, _make_entry(session, bad.id).id, scheduled_date=_FIXED_AS_OF_DATE)

    as_of = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    with caplog.at_level(logging.WARNING):
        count = digest_scheduler.run_wakeup_tick(session=session, as_of_utc=as_of)

    assert count == 1
    session.refresh(good)
    assert good.wakeup_notification_sent_at == as_of


def test_run_wakeup_tick_includes_the_next_day(session, digest_scheduler):
    user = _make_user(session, email="a@example.com", google_sub="a-sub", review_time="09:00", tz="UTC")
    _add_pending_review(session, _make_entry(session, user.id).id, scheduled_date=_FIXED_AS_OF_DATE)

    first_day = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    assert digest_scheduler.run_wakeup_tick(session=session, as_of_utc=first_day) == 1

    next_day = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
    assert digest_scheduler.run_wakeup_tick(session=session, as_of_utc=next_day) == 1

    refreshed = session.exec(select(User).where(User.id == user.id)).one()
    assert refreshed.wakeup_notification_sent_at == next_day


def test_start_scheduler_queues_pregeneration_immediately_and_retains_daily_cron(
    monkeypatch, digest_scheduler
):
    """SRA-907: startup catch-up must not replace the recurring cron contract."""
    from sra.config import get_settings

    started_at = datetime(2026, 10, 14, 17, 0, tzinfo=UTC)
    registered: list[dict[str, object]] = []

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return started_at.replace(tzinfo=None)
            return started_at.astimezone(tz)

    class FakeScheduler:
        def __init__(self, *, timezone):
            assert timezone == "UTC"
            self.started = False

        def add_job(self, func, **kwargs):
            registered.append({"func": func, **kwargs})

        def start(self):
            self.started = True

    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_PREGEN_HOUR_UTC", "4")
    monkeypatch.setenv("SCHEDULER_WAKEUP_INTERVAL_S", "60")
    get_settings.cache_clear()
    monkeypatch.setattr(digest_scheduler, "datetime", FixedDateTime)
    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler", FakeScheduler
    )
    app = FastAPI()

    digest_scheduler.start_scheduler(app)

    pregeneration = next(job for job in registered if job["id"] == "sra.pregenerate")
    assert pregeneration["trigger"] == "cron"
    assert pregeneration["hour"] == 4
    assert pregeneration["minute"] == 0
    assert pregeneration["next_run_time"] == started_at
    assert pregeneration["max_instances"] == 1
    assert pregeneration["coalesce"] is True
    assert app.state.scheduler.started is True
    get_settings.cache_clear()


def test_start_scheduler_disabled_does_not_construct_scheduler_or_catch_up(
    monkeypatch, digest_scheduler
):
    """SRA-907: disabled mode must remain wholly inert, including catch-up work."""
    from sra.config import get_settings

    def unexpected(*args, **kwargs):
        pytest.fail("disabled scheduler attempted to construct or queue work")

    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "apscheduler.schedulers.background.BackgroundScheduler", unexpected
    )
    monkeypatch.setattr(digest_scheduler, "build_pregeneration_job", unexpected)
    app = FastAPI()

    digest_scheduler.start_scheduler(app)

    assert app.state.scheduler is None
    get_settings.cache_clear()
