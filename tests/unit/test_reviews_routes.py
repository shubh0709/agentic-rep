"""SRA-701 — GET /api/v1/reviews/upcoming.

Validates the dashboard's "upcoming topics on various days" endpoint:
authentication, ownership scoping, day-window clamping, day bucketing,
empty-day omission, and the days-query parameter bounds.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from sra.api.rate_limit import limit_authenticated
from sra.auth.dependencies import require_user
from sra.db.models import (
    ReviewSchedule,
    StudyEntry,
    StudyEntryCreate,
)

pytestmark = pytest.mark.phase7


def _seed_entry(session, user_id: int, topic: str = "Photosynthesis") -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic=topic, summary="light reactions", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _seed_review(
    session,
    entry_id: int,
    *,
    interval_label: str,
    scheduled_date: date,
    status: str = "pending",
) -> ReviewSchedule:
    review = ReviewSchedule(
        entry_id=entry_id,
        interval_label=interval_label,
        scheduled_date=scheduled_date,
        status=status,
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def test_upcoming_returns_pending_reviews_after_today(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    today = datetime.now(UTC).date()
    _seed_review(session, entry.id, interval_label="3d", scheduled_date=today + timedelta(days=1))
    _seed_review(session, entry.id, interval_label="7d", scheduled_date=today + timedelta(days=5))

    response = client.get("/api/v1/reviews/upcoming")

    assert response.status_code == 200
    body = response.json()
    days = {item["date"] for item in body}
    assert (today + timedelta(days=1)).isoformat() in days
    assert (today + timedelta(days=5)).isoformat() in days


def test_upcoming_excludes_today_and_past_reviews(client, session, seeded_user):
    """`/digest` owns today and earlier; /reviews/upcoming must not duplicate."""
    entry = _seed_entry(session, seeded_user.id)
    today = datetime.now(UTC).date()
    _seed_review(session, entry.id, interval_label="1d", scheduled_date=today)
    _seed_review(session, entry.id, interval_label="7d", scheduled_date=today - timedelta(days=2))

    response = client.get("/api/v1/reviews/upcoming")

    assert response.status_code == 200
    assert response.json() == []


def test_upcoming_groups_multiple_reviews_for_the_same_day(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    other = _seed_entry(session, seeded_user.id, topic="Mitosis")
    today = datetime.now(UTC).date()
    same_day = today + timedelta(days=3)
    _seed_review(session, entry.id, interval_label="7d", scheduled_date=same_day)
    _seed_review(session, other.id, interval_label="7d", scheduled_date=same_day)

    response = client.get("/api/v1/reviews/upcoming")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["date"] == same_day.isoformat()
    assert len(body[0]["reviews"]) == 2
    labels = {review["interval_label"] for review in body[0]["reviews"]}
    assert labels == {"7d"}


def test_upcoming_excludes_completed_reviews(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    today = datetime.now(UTC).date()
    _seed_review(
        session, entry.id,
        interval_label="3d",
        scheduled_date=today + timedelta(days=1),
        status="done",
    )

    response = client.get("/api/v1/reviews/upcoming")

    assert response.status_code == 200
    assert response.json() == []


def test_upcoming_is_isolated_per_user(client, session, seeded_user, other_user):
    """User A's reviews never leak into user B's response."""
    my_entry = _seed_entry(session, seeded_user.id)
    their_entry = _seed_entry(session, other_user.id, topic="Theirs")
    today = datetime.now(UTC).date()
    target = today + timedelta(days=2)
    _seed_review(session, my_entry.id, interval_label="3d", scheduled_date=target)
    _seed_review(session, their_entry.id, interval_label="3d", scheduled_date=target)

    from sra.auth.google_oauth import issue_app_credential

    other_token = issue_app_credential(
        other_user.id,
        signing_secret="rate-limit-test-signing-secret-with-entropy",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    other_client = TestClient(client.app, headers={"Authorization": f"Bearer {other_token}"})

    me = client.get("/api/v1/reviews/upcoming").json()
    them = other_client.get("/api/v1/reviews/upcoming").json()

    assert me and them
    for item in me:
        assert all(review["entry"]["topic"] == "Photosynthesis" for review in item["reviews"])
    for item in them:
        assert all(review["entry"]["topic"] == "Theirs" for review in item["reviews"])


def test_upcoming_respects_days_query_parameter(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    today = datetime.now(UTC).date()
    _seed_review(session, entry.id, interval_label="3d", scheduled_date=today + timedelta(days=3))
    _seed_review(session, entry.id, interval_label="1mo", scheduled_date=today + timedelta(days=25))

    short = client.get("/api/v1/reviews/upcoming?days=7").json()
    long = client.get("/api/v1/reviews/upcoming?days=60").json()

    short_dates = {item["date"] for item in short}
    long_dates = {item["date"] for item in long}

    assert (today + timedelta(days=3)).isoformat() in short_dates
    assert (today + timedelta(days=25)).isoformat() not in short_dates
    assert (today + timedelta(days=25)).isoformat() in long_dates


def test_upcoming_rejects_days_below_one(client):
    response = client.get("/api/v1/reviews/upcoming?days=0")
    assert response.status_code == 422


def test_upcoming_rejects_days_above_365(client):
    response = client.get("/api/v1/reviews/upcoming?days=500")
    assert response.status_code == 422


def test_upcoming_requires_authentication(client, session, seeded_user):
    anon = TestClient(client.app)
    response = anon.get("/api/v1/reviews/upcoming")
    assert response.status_code == 401


def test_upcoming_route_inherits_authenticated_rate_limit(client, session, seeded_user, monkeypatch):
    """`limit_authenticated` must wrap `/reviews/upcoming` like every other user route."""
    from sra.api.app import create_app
    from sra.config import get_settings
    from sra.db.session import get_session

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "rate-limit-test-signing-secret-with-entropy")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_CAPACITY", "2")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND", "100")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    headers = client.headers

    with TestClient(app) as low_client:
        assert low_client.get("/api/v1/reviews/upcoming", headers=headers).status_code == 200
        assert low_client.get("/api/v1/reviews/upcoming", headers=headers).status_code == 200
        limited = low_client.get("/api/v1/reviews/upcoming", headers=headers)
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers


def test_upcoming_route_is_mounted_under_user_policy(client):
    """Route-policy audit mirror for `/api/v1/reviews/upcoming`."""
    calls_by_path: dict[tuple[str, str], set] = {}

    def dependencies(dependant):
        calls = {dependant.call}
        for child in dependant.dependencies:
            calls.update(dependencies(child))
        return calls

    for route in iter_route_contexts(client.app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        if route.path != "/api/v1/reviews/upcoming":
            continue
        assert route.methods is not None
        for method in route.methods:
            calls_by_path[(method, route.path)] = dependencies(route.dependant)

    assert ("GET", "/api/v1/reviews/upcoming") in calls_by_path
    calls = calls_by_path[("GET", "/api/v1/reviews/upcoming")]
    assert require_user in calls
    assert limit_authenticated in calls


def test_upcoming_omits_empty_days_from_the_response(client, session, seeded_user):
    """A day with no reviews should never produce a tile, even if it falls inside the window."""
    entry = _seed_entry(session, seeded_user.id)
    today = datetime.now(UTC).date()
    # Only one review on day +5; days +1/+2/+3/+4 must be omitted, not rendered empty.
    _seed_review(session, entry.id, interval_label="7d", scheduled_date=today + timedelta(days=5))

    response = client.get("/api/v1/reviews/upcoming?days=10")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["date"] == (today + timedelta(days=5)).isoformat()