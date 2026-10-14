"""SRA-406 Phase 4 per-user rate-limit inheritance: quiz routes share the bucket."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.session import get_session

pytestmark = pytest.mark.phase4
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


def test_quiz_today_route_inherits_per_user_bucket(limited_app, seeded_user):
    app, clock = limited_app
    with TestClient(app) as auth_client:
        first = auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user))
        second = auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user))
        limited = auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user))

        assert first.status_code == 200
        assert second.status_code == 200
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "2"
        assert app.state.rate_limits.authenticated.summary().active_bucket_count >= 1


def test_quiz_today_and_existing_protected_routes_share_a_user_bucket(wide_limited_app, seeded_user):
    """A burst that mixes quiz and existing routes hits 429 from the same bucket."""

    app, clock = wide_limited_app
    with TestClient(app) as auth_client:
        responses = [
            auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user)),
            auth_client.get("/api/v1/search", headers=_headers(seeded_user)),
            auth_client.get("/api/v1/digest", headers=_headers(seeded_user)),
            auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user)),
        ]
        assert all(r.status_code == 200 for r in responses)

        limited = auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user))
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "2"

        clock[0] += 1.5
        refill = auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user))
        assert refill.headers["Retry-After"] == "1"


def test_quiz_today_route_separate_users_have_independent_buckets(limited_app, seeded_user, other_user):
    app, clock = limited_app
    with TestClient(app) as auth_client:
        assert auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user)).status_code == 200
        assert auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user)).status_code == 200
        assert auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user)).status_code == 429
        assert auth_client.get("/api/v1/quiz/today", headers=_headers(other_user)).status_code == 200


def test_quiz_routes_include_limit_authenticated_dependency(client):
    from fastapi.routing import APIRoute, iter_route_contexts

    from sra.api.rate_limit import limit_authenticated
    from sra.auth.dependencies import require_user

    quiz_paths = set()
    for route in iter_route_contexts(client.app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        if route.path is not None and route.path.startswith("/api/v1/quiz"):

            def collect(dependant):
                calls = {dependant.call}
                for child in dependant.dependencies:
                    calls.update(collect(child))
                return calls

            for method in route.methods or {"GET"}:
                quiz_paths.add((method, route.path))
            calls = collect(route.dependant)
            assert require_user in calls
            assert limit_authenticated in calls

    assert quiz_paths, "no /api/v1/quiz routes are mounted yet"