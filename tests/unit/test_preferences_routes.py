"""SRA-501 — Preferences router: GET / PUT /api/v1/me/preferences."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.models import User
from sra.db.session import get_session

pytestmark = pytest.mark.phase5

SECRET = "rate-limit-test-signing-secret-with-entropy"


def _credentials_for(user) -> dict[str, str]:
    token = issue_app_credential(
        user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    return {"Authorization": f"Bearer {token}"}


def _new_client(monkeypatch, session, *, capacity: int | None = None) -> TestClient:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    if capacity is not None:
        monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_CAPACITY", str(capacity))
        monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND", "100")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_get_preferences_returns_defaults_for_a_fresh_user(client, seeded_user):
    response = client.get("/api/v1/me/preferences")

    assert response.status_code == 200
    assert response.json() == {
        "preferred_review_time": "09:00",
        "timezone": "UTC",
        "quiz_question_count": 10,
        "quiz_option_count": 4,
        "interview_question_count": 6,
        "interview_target_minutes": 5,
        "audio_enabled": True,
        "wakeup_notification_sent_at": None,
    }


def test_put_preferences_round_trips_valid_body(client, session, seeded_user):
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "07:30",
            "timezone": "America/Los_Angeles",
            "quiz_question_count": 12,
            "quiz_option_count": 5,
            "interview_question_count": 6,
            "interview_target_minutes": 5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preferred_review_time"] == "07:30"
    assert body["timezone"] == "America/Los_Angeles"
    assert body["quiz_question_count"] == 12
    assert body["quiz_option_count"] == 5
    assert body["interview_question_count"] == 6
    assert body["interview_target_minutes"] == 5

    stored = session.exec(select(User).where(User.id == seeded_user.id)).one()
    assert stored.preferred_review_time == "07:30"
    assert stored.timezone == "America/Los_Angeles"
    assert stored.quiz_question_count == 12
    assert stored.quiz_option_count == 5
    assert stored.interview_question_count == 6
    assert stored.interview_target_minutes == 5


def test_put_preferences_accepts_legacy_4_field_body_and_keeps_interview_defaults(client, session, seeded_user):
    """Regression guard for SRA-801→SRA-809 customer escape valve.

    The web ``PUT /api/v1/me/preferences`` form originally sent only the four
    legacy quiz fields. Phase 8 promoted two extra ``interview_*`` columns to
    the API surface. The router and schema must keep backward compatibility
    so an existing form continues to round-trip successfully without forcing
    every caller to learn about interview fields.

    Sent fields update; omitted fields keep their persisted DB value
    (defaults for a fresh user).
    """
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "08:15",
            "timezone": "America/New_York",
            "quiz_question_count": 15,
            "quiz_option_count": 5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preferred_review_time"] == "08:15"
    assert body["timezone"] == "America/New_York"
    assert body["quiz_question_count"] == 15
    assert body["quiz_option_count"] == 5
    # Interview fields omitted → defaults preserved on the row.
    assert body["interview_question_count"] == 6
    assert body["interview_target_minutes"] == 5


def test_put_preferences_partial_update_preserves_other_interview_field(client, session, seeded_user):
    """When exactly one interview field is supplied, the other stays put."""
    seeded_user.interview_question_count = 9
    seeded_user.interview_target_minutes = 7
    session.add(seeded_user)
    session.commit()
    session.refresh(seeded_user)

    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "07:00",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
            "interview_question_count": 12,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["interview_question_count"] == 12
    assert body["interview_target_minutes"] == 7


def test_put_preferences_round_trips_audio_and_legacy_body_preserves_it(
    client, session, seeded_user
):
    disable = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
            "audio_enabled": False,
        },
    )
    assert disable.status_code == 200
    assert disable.json()["audio_enabled"] is False

    legacy_update = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "08:15",
            "timezone": "UTC",
            "quiz_question_count": 8,
            "quiz_option_count": 4,
        },
    )
    assert legacy_update.status_code == 200
    assert legacy_update.json()["audio_enabled"] is False

    stored = session.exec(select(User).where(User.id == seeded_user.id)).one()
    assert stored.audio_enabled is False


def test_put_preferences_rejects_non_boolean_audio_value(client):
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
            "audio_enabled": "yes",
        },
    )

    assert response.status_code == 400
    assert {error["field"] for error in response.json()["detail"]} == {
        "audio_enabled"
    }


def test_put_preferences_rejects_invalid_legacy_4_field_inputs(client):
    """The 4-field form must still validate the legacy fields it sends."""
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "9am",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
        },
    )
    assert response.status_code == 400

    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "timezone": "IST",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    fields = {err["field"] for err in detail}
    assert "timezone" in fields


def test_put_preferences_preserves_wakeup_sent_at(client, session, seeded_user):
    seeded_user.wakeup_notification_sent_at = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    session.add(seeded_user)
    session.commit()
    session.refresh(seeded_user)

    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "08:00",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
            "interview_question_count": 6,
            "interview_target_minutes": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["wakeup_notification_sent_at"] is not None


def test_put_preferences_rejects_invalid_hhmm(client):
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "99:99",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
        },
    )
    assert response.status_code == 400


def test_put_preferences_rejects_invalid_timezone(client):
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "timezone": "Mars/Olympus",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
        },
    )
    assert response.status_code == 400


def test_put_preferences_rejects_out_of_range_question_count(client):
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "timezone": "UTC",
            "quiz_question_count": 0,
            "quiz_option_count": 4,
        },
    )
    assert response.status_code == 400


def test_put_preferences_rejects_out_of_range_option_count(client):
    response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 1,
        },
    )
    assert response.status_code == 400


def test_get_preferences_requires_authentication(client, session, seeded_user):
    anon = TestClient(client.app)
    response = anon.get("/api/v1/me/preferences")
    assert response.status_code == 401


def test_put_preferences_requires_authentication(client, session, seeded_user):
    anon = TestClient(client.app)
    response = anon.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "08:00",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
        },
    )
    assert response.status_code == 401


def test_get_preferences_is_scoped_to_the_calling_user(monkeypatch, session, seeded_user, other_user):
    seeded_user.preferred_review_time = "07:30"
    seeded_user.timezone = "America/Los_Angeles"
    session.add(seeded_user)
    session.commit()
    session.refresh(seeded_user)

    other_token_client = _new_client(monkeypatch, session)
    headers = _credentials_for(other_user)
    response = other_token_client.get("/api/v1/me/preferences", headers=headers)

    assert response.status_code == 200
    assert response.json()["preferred_review_time"] == "09:00"
    assert response.json()["timezone"] == "UTC"


def test_put_preferences_is_scoped_to_the_calling_user(monkeypatch, session, seeded_user, other_user):
    other_token_client = _new_client(monkeypatch, session)
    headers = _credentials_for(other_user)
    response = other_token_client.put(
        "/api/v1/me/preferences",
        headers=headers,
        json={
            "preferred_review_time": "06:00",
            "timezone": "Europe/Berlin",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
            "interview_question_count": 6,
            "interview_target_minutes": 5,
        },
    )
    assert response.status_code == 200

    seeded_again = session.exec(select(User).where(User.id == seeded_user.id)).one()
    assert seeded_again.preferred_review_time == "09:00"
    assert seeded_again.timezone == "UTC"


def test_preferences_routes_use_the_authenticated_rate_limit(monkeypatch, session, seeded_user):
    """Smoke test: the preferences routes inherit limit_authenticated like the rest
    of the protected routers. A low bucket capacity must yield a 429 with
    Retry-After once exhausted."""
    low_client = _new_client(monkeypatch, session, capacity=2)
    headers = _credentials_for(seeded_user)
    assert low_client.get("/api/v1/me/preferences", headers=headers).status_code == 200
    assert low_client.get("/api/v1/me/preferences", headers=headers).status_code == 200
    limited = low_client.get("/api/v1/me/preferences", headers=headers)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
