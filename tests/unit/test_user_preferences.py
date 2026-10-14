"""SRA-501 — User preferences columns, repository helpers, and schemas."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from sra.db import repository
from sra.db.models import User, UserCreate

pytestmark = pytest.mark.phase5

MAX_QUESTION_COUNT = 20
MAX_OPTION_COUNT = 6


def _make_user(**overrides) -> User:
    payload = {
        "email": "prefs@example.com",
        "display_name": "Prefs User",
        "google_sub": "prefs-sub",
    }
    payload.update(overrides)
    return User.model_validate(UserCreate(**payload), update={})


def test_defaults_match_design_for_a_freshly_created_user():
    user = _make_user()

    assert user.preferred_review_time == "09:00"
    assert user.timezone == "UTC"
    assert user.quiz_question_count == 10
    assert user.quiz_option_count == 4
    assert user.wakeup_notification_sent_at is None


@pytest.mark.parametrize("hour", ["00:00", "09:00", "12:30", "23:59"])
def test_valid_hhmm_review_times_are_accepted(hour):
    user = _make_user(preferred_review_time=hour)
    assert user.preferred_review_time == hour


@pytest.mark.parametrize("bad", ["24:00", "9:00", "09:60", "9:5", "abc", "0900", "09-00", ""])
def test_invalid_hhmm_review_times_are_rejected(bad):
    with pytest.raises(ValidationError):
        _make_user(preferred_review_time=bad)


@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Europe/Berlin", "Asia/Kolkata"])
def test_valid_iana_timezones_are_accepted(zone):
    user = _make_user(timezone=zone)
    assert user.timezone == zone


@pytest.mark.parametrize("zone", ["Mars/Olympus", "America/Atlantis", "", "PST", "  "])
def test_invalid_timezones_are_rejected(zone):
    with pytest.raises(ValidationError):
        _make_user(timezone=zone)


@pytest.mark.parametrize("count", [1, 5, 10, 20])
def test_valid_question_counts_are_accepted(count):
    user = _make_user(quiz_question_count=count)
    assert user.quiz_question_count == count


@pytest.mark.parametrize("count", [0, -1, 21, 99, MAX_QUESTION_COUNT + 1])
def test_question_count_out_of_range_is_rejected(count):
    with pytest.raises(ValidationError):
        _make_user(quiz_question_count=count)


@pytest.mark.parametrize("count", [2, 3, 4, 6])
def test_valid_option_counts_are_accepted(count):
    user = _make_user(quiz_option_count=count)
    assert user.quiz_option_count == count


@pytest.mark.parametrize("count", [0, 1, 7, 10])
def test_option_count_out_of_range_is_rejected(count):
    with pytest.raises(ValidationError):
        _make_user(quiz_option_count=count)


def test_database_rejects_invalid_hhmm_at_the_check_constraint_boundary(session, seeded_user):
    """The model validator catches bad input; the CheckConstraint is a second wall."""
    seeded_user.preferred_review_time = "9:00"
    session.add(seeded_user)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_database_rejects_out_of_range_counts(session, seeded_user):
    seeded_user.quiz_question_count = MAX_QUESTION_COUNT + 1
    session.add(seeded_user)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_database_rejects_out_of_range_option_counts(session, seeded_user):
    seeded_user.quiz_option_count = 1
    session.add(seeded_user)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_wakeup_notification_sent_at_starts_null_and_accepts_timezone_aware_datetime(session, seeded_user):
    assert seeded_user.wakeup_notification_sent_at is None
    seeded_user.wakeup_notification_sent_at = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    session.add(seeded_user)
    session.commit()
    session.refresh(seeded_user)
    assert seeded_user.wakeup_notification_sent_at == datetime(2026, 10, 14, 9, 30, tzinfo=UTC)


def test_get_user_preferences_returns_defaults_for_a_fresh_user(session, seeded_user):
    prefs = repository.get_user_preferences(seeded_user.id, session)

    assert prefs == {
        "preferred_review_time": "09:00",
        "timezone": "UTC",
        "quiz_question_count": 10,
        "quiz_option_count": 4,
        "wakeup_notification_sent_at": None,
    }


def test_get_user_preferences_raises_user_not_found_error(session):
    with pytest.raises(repository.UserNotFoundError):
        repository.get_user_preferences(999_999, session)


def test_get_user_preferences_reads_persisted_overrides(session, seeded_user):
    seeded_user.preferred_review_time = "07:30"
    seeded_user.timezone = "America/Los_Angeles"
    seeded_user.quiz_question_count = 5
    seeded_user.quiz_option_count = 3
    seeded_user.wakeup_notification_sent_at = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    session.add(seeded_user)
    session.commit()
    session.refresh(seeded_user)

    prefs = repository.get_user_preferences(seeded_user.id, session)

    assert prefs["preferred_review_time"] == "07:30"
    assert prefs["timezone"] == "America/Los_Angeles"
    assert prefs["quiz_question_count"] == 5
    assert prefs["quiz_option_count"] == 3
    assert prefs["wakeup_notification_sent_at"] == datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


def test_update_user_preferences_persists_and_returns_new_shape(session, seeded_user):
    payload = {
        "preferred_review_time": "06:45",
        "timezone": "Europe/Berlin",
        "quiz_question_count": 12,
        "quiz_option_count": 5,
    }

    prefs = repository.update_user_preferences(seeded_user.id, payload, session)

    assert prefs["preferred_review_time"] == "06:45"
    assert prefs["timezone"] == "Europe/Berlin"
    assert prefs["quiz_question_count"] == 12
    assert prefs["quiz_option_count"] == 5
    assert prefs["wakeup_notification_sent_at"] is None

    stored = session.exec(select(User).where(User.id == seeded_user.id)).one()
    assert stored.preferred_review_time == "06:45"
    assert stored.timezone == "Europe/Berlin"
    assert stored.quiz_question_count == 12
    assert stored.quiz_option_count == 5


def test_update_user_preferences_raises_user_not_found_error(session):
    with pytest.raises(repository.UserNotFoundError):
        repository.update_user_preferences(999_999, {"preferred_review_time": "08:00"}, session)


def test_update_user_preferences_does_not_clear_wakeup_sent_at(session, seeded_user):
    seeded_user.wakeup_notification_sent_at = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    session.add(seeded_user)
    session.commit()
    session.refresh(seeded_user)

    prefs = repository.update_user_preferences(
        seeded_user.id, {"preferred_review_time": "08:00"}, session
    )

    assert prefs["wakeup_notification_sent_at"] == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def test_user_preferences_typed_dict_exposes_documented_keys():
    """Repository exposes UserPreferences; the helper exists and the keys are stable."""
    helper = getattr(repository, "UserPreferences", None)
    assert helper is not None
    annotations = getattr(helper, "__annotations__", {})
    assert set(annotations) == {
        "preferred_review_time",
        "timezone",
        "quiz_question_count",
        "quiz_option_count",
        "wakeup_notification_sent_at",
    }


def test_preferences_in_schema_round_trips_valid_payload():
    from sra.api.schemas import PreferencesIn, PreferencesOut

    payload = PreferencesIn(
        preferred_review_time="07:30",
        timezone="America/Los_Angeles",
        quiz_question_count=10,
        quiz_option_count=4,
    )

    assert payload.preferred_review_time == "07:30"
    assert payload.timezone == "America/Los_Angeles"
    assert payload.quiz_question_count == 10
    assert payload.quiz_option_count == 4

    out = PreferencesOut(**payload.model_dump())
    assert out.model_dump() == payload.model_dump()


@pytest.mark.parametrize(
    "field, value",
    [
        ("preferred_review_time", "9:00"),
        ("preferred_review_time", "24:00"),
        ("timezone", "America/Atlantis"),
        ("quiz_question_count", 0),
        ("quiz_question_count", 99),
        ("quiz_option_count", 1),
        ("quiz_option_count", 99),
    ],
)
def test_preferences_in_schema_rejects_invalid_payloads(field, value):
    from sra.api.schemas import PreferencesIn

    base = {
        "preferred_review_time": "09:00",
        "timezone": "UTC",
        "quiz_question_count": 10,
        "quiz_option_count": 4,
    }
    base[field] = value
    with pytest.raises(ValidationError):
        PreferencesIn(**base)


def test_preferences_in_schema_rejects_extra_fields():
    from sra.api.schemas import PreferencesIn

    payload = {
        "preferred_review_time": "09:00",
        "timezone": "UTC",
        "quiz_question_count": 10,
        "quiz_option_count": 4,
        "unexpected_field": "nope",
    }
    with pytest.raises(ValidationError):
        PreferencesIn.model_validate(payload)


def test_user_preferences_module_invariants(session):
    """Repository: UserNotFoundError exists, prefers the canonical import path."""
    assert hasattr(repository, "UserNotFoundError")
    assert issubclass(repository.UserNotFoundError, Exception)
