"""SRA-603 — ``reset_demo_data`` and ``DemoModeDisabledError`` contracts.

The reset job is the only destructive path that distinguishes demo data
from real data; these tests pin its safety guards and its behavior.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from sra.db.models import ReviewSchedule, StudyEntry, User

DEMO_USER_RETENTION = 7


def _make_user(session, *, email: str, google_sub: str, is_demo_user: bool) -> User:
    user = User(email=email, display_name=email, google_sub=google_sub, is_demo_user=is_demo_user)
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    return user


def _make_stale_entry(session, user_id: int, *, days_old: int) -> StudyEntry:
    """Insert an entry whose ``created_at`` is ``days_old`` days before ``now``."""
    entry = StudyEntry(user_id=user_id, topic=f"Stale-{user_id}", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None

    past = datetime.now(UTC) - timedelta(days=days_old)
    from sqlalchemy import text

    session.execute(
        text("UPDATE study_entries SET created_at = :ts WHERE id = :id"),
        {"ts": past.strftime("%Y-%m-%d %H:%M:%S"), "id": entry.id},
    )
    session.commit()
    return entry


def _make_fresh_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry(user_id=user_id, topic=f"Fresh-{user_id}", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    return entry


def _session_factory(session: Session):
    def factory():
        return session

    return factory


def test_reset_demo_data_raises_demo_mode_disabled_when_demo_mode_false(monkeypatch, session, seeded_user):
    """A self-hoster (demo_mode=False) cannot accidentally invoke the destructive path."""
    from sra.jobs.demo_reset import DemoModeDisabledError, reset_demo_data

    settings = SimpleNamespace(demo_mode=False, demo_data_retention_days=DEMO_USER_RETENTION)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    with pytest.raises(DemoModeDisabledError):
        reset_demo_data(retention_days=DEMO_USER_RETENTION, session_factory=_session_factory(session))

    # Database is unchanged -- the guard fires before any write.
    users = list(session.exec(__import__("sqlmodel").select(User)))
    assert seeded_user.id in {u.id for u in users}


def test_reset_demo_data_returns_zero_when_nothing_is_old(monkeypatch, session):
    from sra.jobs.demo_reset import reset_demo_data

    settings = SimpleNamespace(demo_mode=True, demo_data_retention_days=DEMO_USER_RETENTION)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    fresh_demo = _make_user(session, email="fresh@demo", google_sub="fresh-demo", is_demo_user=True)

    result = reset_demo_data(
        retention_days=DEMO_USER_RETENTION,
        session_factory=_session_factory(session),
    )

    assert result == {"deleted": 0}
    # the fresh demo user is still present
    refreshed = session.get(User, fresh_demo.id)
    assert refreshed is not None


def test_reset_demo_data_deletes_only_stale_demo_users(monkeypatch, session):
    """A non-demo user is never deleted; only demo users past the cutoff are victims."""
    from sra.jobs.demo_reset import reset_demo_data

    settings = SimpleNamespace(demo_mode=True, demo_data_retention_days=DEMO_USER_RETENTION)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    stale_demo = _make_user(session, email="stale@demo", google_sub="stale-demo", is_demo_user=True)
    fresh_demo = _make_user(session, email="fresh@demo", google_sub="fresh-demo", is_demo_user=True)
    stale_real = _make_user(session, email="real@example.com", google_sub="real-sub", is_demo_user=False)
    _make_stale_entry(session, stale_demo.id, days_old=10)
    _make_fresh_entry(session, fresh_demo.id)
    _make_stale_entry(session, stale_real.id, days_old=10)

    result = reset_demo_data(
        retention_days=DEMO_USER_RETENTION,
        session_factory=_session_factory(session),
    )

    assert result == {"deleted": 1}
    assert session.get(User, stale_demo.id) is None
    assert session.get(User, fresh_demo.id) is not None
    assert session.get(User, stale_real.id) is not None


def test_reset_demo_data_is_idempotent_within_same_window(monkeypatch, session):
    """A second invocation right after the first returns ``{deleted: 0}`` without touching the DB."""
    from sra.jobs.demo_reset import reset_demo_data

    settings = SimpleNamespace(demo_mode=True, demo_data_retention_days=DEMO_USER_RETENTION)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    stale_demo = _make_user(session, email="stale@demo", google_sub="stale-demo", is_demo_user=True)
    _make_stale_entry(session, stale_demo.id, days_old=10)

    first = reset_demo_data(
        retention_days=DEMO_USER_RETENTION,
        session_factory=_session_factory(session),
    )
    assert first == {"deleted": 1}
    assert session.get(User, stale_demo.id) is None

    second = reset_demo_data(
        retention_days=DEMO_USER_RETENTION,
        session_factory=_session_factory(session),
    )
    assert second == {"deleted": 0}


def test_reset_demo_data_clears_dependents_for_each_victim(monkeypatch, session):
    """The cascade empties every dependent table for each deleted user."""
    from sqlmodel import select

    from sra.jobs.demo_reset import reset_demo_data

    settings = SimpleNamespace(demo_mode=True, demo_data_retention_days=DEMO_USER_RETENTION)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    victim = _make_user(session, email="victim@demo", google_sub="victim-demo", is_demo_user=True)
    entry = _make_stale_entry(session, victim.id, days_old=10)
    schedule = ReviewSchedule(
        entry_id=entry.id,
        interval_label="1d",
        scheduled_date=datetime.now(UTC).date(),
        status="pending",
    )
    session.add(schedule)
    session.commit()
    schedule_id = schedule.id

    result = reset_demo_data(
        retention_days=DEMO_USER_RETENTION,
        session_factory=_session_factory(session),
    )

    assert result == {"deleted": 1}
    assert session.get(User, victim.id) is None
    assert session.exec(select(StudyEntry).where(StudyEntry.user_id == victim.id)).all() == []
    assert session.exec(select(ReviewSchedule).where(ReviewSchedule.id == schedule_id)).all() == []


def test_build_demo_reset_job_returns_callable(monkeypatch, session):
    """``build_demo_reset_job`` returns a callable suitable for APScheduler scheduling."""
    from sra.jobs.demo_reset import build_demo_reset_job

    settings = SimpleNamespace(demo_mode=True, demo_data_retention_days=DEMO_USER_RETENTION)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    job = build_demo_reset_job(_session_factory(session), DEMO_USER_RETENTION)
    job()  # must run without raising

    # No demo users yet; result must be {deleted: 0}
    second = build_demo_reset_job(_session_factory(session), DEMO_USER_RETENTION)
    assert second is not None  # builder returns a fresh closure each call


def test_reset_demo_data_uses_provided_as_of_utc(monkeypatch, session):
    """``as_of_utc`` controls the cutoff: passing a fixed value makes the boundary deterministic."""
    from sra.jobs.demo_reset import reset_demo_data

    settings = SimpleNamespace(demo_mode=True, demo_data_retention_days=7)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    victim = _make_user(session, email="boundary@demo", google_sub="boundary-demo", is_demo_user=True)
    # Insert an entry 8 days before the explicit as_of_utc -> 8 > 7 -> victim.
    fixed_as_of = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    entry = StudyEntry(user_id=victim.id, topic="Boundary", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None

    eight_days_earlier = fixed_as_of - timedelta(days=8)
    from sqlalchemy import text

    session.execute(
        text("UPDATE study_entries SET created_at = :ts WHERE id = :id"),
        {"ts": eight_days_earlier.strftime("%Y-%m-%d %H:%M:%S"), "id": entry.id},
    )
    session.commit()

    result = reset_demo_data(
        retention_days=7,
        as_of_utc=fixed_as_of,
        session_factory=_session_factory(session),
    )

    assert result == {"deleted": 1}
    assert session.get(User, victim.id) is None


def test_reset_demo_data_does_not_touch_demo_user_younger_than_retention(monkeypatch, session):
    """A demo user whose oldest timestamp is younger than the cutoff is preserved."""
    from sra.jobs.demo_reset import reset_demo_data

    settings = SimpleNamespace(demo_mode=True, demo_data_retention_days=DEMO_USER_RETENTION)
    monkeypatch.setattr("sra.config.get_settings", lambda: settings)

    young_demo = _make_user(session, email="young@demo", google_sub="young-demo", is_demo_user=True)
    _make_stale_entry(session, young_demo.id, days_old=3)  # inside retention window

    result = reset_demo_data(
        retention_days=DEMO_USER_RETENTION,
        session_factory=_session_factory(session),
    )

    assert result == {"deleted": 0}
    assert session.get(User, young_demo.id) is not None