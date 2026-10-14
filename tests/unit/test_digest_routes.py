"""SRA-502 — GET /api/v1/digest/ready."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import UTC, datetime

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from sra.agents.schemas import MCQDraft
from sra.api.rate_limit import limit_authenticated
from sra.auth.dependencies import require_user
from sra.db.models import (
    ReviewSchedule,
    StudyEntry,
    StudyEntryCreate,
)
from sra.db.repository import create_quiz_set

pytestmark = pytest.mark.phase5


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _seed_pending_review_today(session, entry_id: int) -> ReviewSchedule:
    review = ReviewSchedule(
        entry_id=entry_id,
        interval_label="1d",
        scheduled_date=datetime.now(UTC).date(),
        status="pending",
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def test_digest_ready_returns_true_when_due_reviews_and_quiz_set_exist(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    _seed_pending_review_today(session, entry.id)
    create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=datetime.now(UTC).date(),
        question_count=1,
        option_count=2,
        drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")],
    )

    response = client.get("/api/v1/digest/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_digest_ready_returns_false_when_no_quiz_set_yet(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    _seed_pending_review_today(session, entry.id)

    response = client.get("/api/v1/digest/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": False}


def test_digest_ready_returns_false_when_no_pending_reviews(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=datetime.now(UTC).date(),
        question_count=1,
        option_count=2,
        drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")],
    )

    response = client.get("/api/v1/digest/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": False}


def test_digest_ready_is_isolated_per_user(client, session, seeded_user, other_user):
    """User A has a complete digest; user B sees ready=False because B has no
    due reviews and no quiz set of their own."""
    entry = _seed_entry(session, seeded_user.id)
    _seed_pending_review_today(session, entry.id)
    create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=datetime.now(UTC).date(),
        question_count=1,
        option_count=2,
        drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")],
    )

    from sra.auth.google_oauth import issue_app_credential

    other_token = issue_app_credential(
        other_user.id,
        signing_secret="rate-limit-test-signing-secret-with-entropy",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    other_client = TestClient(client.app, headers={"Authorization": f"Bearer {other_token}"})
    response = other_client.get("/api/v1/digest/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": False}


def test_digest_ready_requires_authentication(client, session, seeded_user):
    anon = TestClient(client.app)
    response = anon.get("/api/v1/digest/ready")
    assert response.status_code == 401


def test_digest_ready_uses_authenticated_rate_limit(client, session, seeded_user, monkeypatch):
    """Smoke test: the digest/ready route inherits limit_authenticated. A controlled
    low capacity must yield a 429 with Retry-After once exhausted."""
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
        assert low_client.get("/api/v1/digest/ready", headers=headers).status_code == 200
        assert low_client.get("/api/v1/digest/ready", headers=headers).status_code == 200
        limited = low_client.get("/api/v1/digest/ready", headers=headers)
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers


def test_digest_ready_route_is_mounted_under_user_policy(client):
    """The route-policy audit covers the new /digest/ready route under the
    same 'user' policy as the rest of the protected routers."""
    calls_by_path: dict[tuple[str, str], set] = {}

    def dependencies(dependant):
        calls = {dependant.call}
        for child in dependant.dependencies:
            calls.update(dependencies(child))
        return calls

    for route in iter_route_contexts(client.app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        if route.path != "/api/v1/digest/ready":
            continue
        assert route.methods is not None
        for method in route.methods:
            calls_by_path[(method, route.path)] = dependencies(route.dependant)

    assert ("GET", "/api/v1/digest/ready") in calls_by_path
    calls = calls_by_path[("GET", "/api/v1/digest/ready")]
    assert require_user in calls
    assert limit_authenticated in calls
