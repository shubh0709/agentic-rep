"""SRA-509 Phase 5 per-user rate-limit inheritance.

The new `/me/preferences`, `/digest/ready`, and `/quiz/attempts/{id}/voice-turn`
routes share the same per-user token-bucket as the existing protected routers
(entries, digest, search, chat, quiz). This file proves the inheritance for the
Phase 5 surfaces and re-confirms that the route policy audit is still clean.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.session import get_session

pytestmark = pytest.mark.phase5
SECRET = "rate-limit-test-signing-secret-with-entropy"


@pytest.fixture
def limited_app(monkeypatch, session):
    for key, value in {
        "OPENAI_API_KEY": "test",
        "AUTH_SIGNING_SECRET": SECRET,
        "GOOGLE_CLIENT_ID": "test-client",
        "GOOGLE_CLIENT_SECRET": "test-secret",
        "RATE_LIMIT_AUTHENTICATED_CAPACITY": "2",
        "RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND": "0.5",
        "RATE_LIMIT_ANONYMOUS_CAPACITY": "2",
        "RATE_LIMIT_ANONYMOUS_REFILL_PER_SECOND": "0.5",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    clock = [1000.0]
    app = create_app(rate_limit_clock=lambda: clock[0])
    app.dependency_overrides[get_session] = lambda: session
    return app, clock


@pytest.fixture
def wide_limited_app(monkeypatch, session):
    for key, value in {
        "OPENAI_API_KEY": "test",
        "AUTH_SIGNING_SECRET": SECRET,
        "GOOGLE_CLIENT_ID": "test-client",
        "GOOGLE_CLIENT_SECRET": "test-secret",
        "RATE_LIMIT_AUTHENTICATED_CAPACITY": "4",
        "RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND": "0.5",
        "RATE_LIMIT_ANONYMOUS_CAPACITY": "2",
        "RATE_LIMIT_ANONYMOUS_REFILL_PER_SECOND": "0.5",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    clock = [1000.0]
    app = create_app(rate_limit_clock=lambda: clock[0])
    app.dependency_overrides[get_session] = lambda: session
    return app, clock


def _headers(user):
    token = issue_app_credential(user.id, signing_secret=SECRET, ttl_seconds=300, now=datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}


def test_preferences_and_digest_ready_share_per_user_bucket(limited_app, seeded_user):
    app, _clock = limited_app
    with TestClient(app) as auth_client:
        first = auth_client.get("/api/v1/me/preferences", headers=_headers(seeded_user))
        second = auth_client.get("/api/v1/digest/ready", headers=_headers(seeded_user))
        limited = auth_client.get("/api/v1/me/preferences", headers=_headers(seeded_user))

        assert first.status_code == 200
        assert second.status_code == 200
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "2"
        assert app.state.rate_limits.authenticated.summary().active_bucket_count >= 1


def test_new_and_existing_protected_routes_share_a_user_bucket(wide_limited_app, seeded_user, session):
    """A burst mixing Phase 5 and existing routes hits 429 from the same bucket."""

    from datetime import date

    from sra.agents.schemas import MCQDraft
    from sra.db.models import StudyEntry, StudyEntryCreate
    from sra.db.repository import create_quiz_set
    from sra.jobs.quiz_pregeneration import pregenerate_quizzes_for_day

    drafts = [
        MCQDraft(question_text="q", options=["a", "b", "c", "d"], correct_option_index=0, explanation="")
    ]
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": seeded_user.id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=drafts,
    )
    pregenerate_quizzes_for_day(today=date.today(), session=session)

    app, clock = wide_limited_app
    with TestClient(app) as auth_client:
        responses = [
            auth_client.get("/api/v1/me/preferences", headers=_headers(seeded_user)),
            auth_client.get("/api/v1/digest/ready", headers=_headers(seeded_user)),
            auth_client.get("/api/v1/search", headers=_headers(seeded_user)),
            auth_client.get("/api/v1/digest", headers=_headers(seeded_user)),
        ]
        assert all(r.status_code == 200 for r in responses)

        limited = auth_client.get("/api/v1/me/preferences", headers=_headers(seeded_user))
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "2"

        clock[0] += 1.5
        refill = auth_client.get("/api/v1/me/preferences", headers=_headers(seeded_user))
        assert refill.headers["Retry-After"] == "1"


def test_preferences_route_separate_users_have_independent_buckets(limited_app, seeded_user, other_user):
    app, _clock = limited_app
    with TestClient(app) as auth_client:
        assert auth_client.get("/api/v1/me/preferences", headers=_headers(seeded_user)).status_code == 200
        assert auth_client.get("/api/v1/digest/ready", headers=_headers(seeded_user)).status_code == 200
        assert auth_client.get("/api/v1/me/preferences", headers=_headers(seeded_user)).status_code == 429
        assert auth_client.get("/api/v1/me/preferences", headers=_headers(other_user)).status_code == 200


def test_phase5_routes_include_limit_authenticated_dependency(client):
    from fastapi.routing import APIRoute, iter_route_contexts

    from sra.api.rate_limit import limit_authenticated
    from sra.auth.dependencies import require_user

    expected_paths = {
        ("GET", "/api/v1/me/preferences"),
        ("PUT", "/api/v1/me/preferences"),
        ("GET", "/api/v1/digest/ready"),
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/voice-turn"),
    }
    seen: set[tuple[str, str]] = set()

    def dependencies(dependant):
        calls = {dependant.call}
        for child in dependant.dependencies:
            calls.update(dependencies(child))
        return calls

    for route in iter_route_contexts(client.app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        assert route.path is not None
        if route.path is None:
            continue
        assert route.methods is not None
        for method in route.methods:
            key = (method, route.path)
            if key in expected_paths:
                calls = dependencies(route.dependant)
                assert require_user in calls
                assert limit_authenticated in calls
                seen.add(key)

    assert seen == expected_paths