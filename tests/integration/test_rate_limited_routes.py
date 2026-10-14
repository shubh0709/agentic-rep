"""Real credentials, dependencies, routes and SQLite with a controlled clock."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.api.app import create_app
from sra.auth.google_oauth import GoogleIdentity, issue_app_credential
from sra.config import get_settings
from sra.db.models import StudyEntry, UserRole
from sra.db.session import get_session

pytestmark = pytest.mark.phase3
SECRET = "rate-limit-test-signing-secret-with-entropy"


@pytest.fixture
def limited_app(monkeypatch, session):
    for key, value in {
        "OPENAI_API_KEY": "test", "AUTH_SIGNING_SECRET": SECRET,
        "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "test-secret",
        "RATE_LIMIT_AUTHENTICATED_CAPACITY": "2", "RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND": "0.5",
        "RATE_LIMIT_ANONYMOUS_CAPACITY": "2", "RATE_LIMIT_ANONYMOUS_REFILL_PER_SECOND": "0.5",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    clock = [1000.0]
    app = create_app(rate_limit_clock=lambda: clock[0])
    app.dependency_overrides[get_session] = lambda: session
    return app, clock


def headers(user):
    token = issue_app_credential(user.id, signing_secret=SECRET, ttl_seconds=300, now=datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}


def test_user_buckets_identity_refill_health_and_authentication_order(limited_app, seeded_user, other_user):
    app, clock = limited_app
    with TestClient(app) as client:
        assert client.get("/api/v1/search").status_code == 401
        assert app.state.rate_limits.authenticated.summary().active_bucket_count == 0
        for path in ("/api/v1/search", "/api/v1/digest"):
            response = client.get(path, headers=headers(seeded_user))
            assert response.status_code == 200
            assert "Retry-After" not in response.headers
        limited = client.get("/api/v1/auth/me?user_id=999", headers=headers(seeded_user))
        assert limited.status_code == 429
        assert limited.json() == {"detail": "Rate limit exceeded"}
        assert limited.headers["Retry-After"] == "2"
        assert client.get("/api/v1/search", headers=headers(other_user)).status_code == 200
        assert client.get("/api/health").json() == {"status": "ok"}
        assert app.state.rate_limits.anonymous.summary().active_bucket_count == 0
        clock[0] += 1.5
        assert client.get("/api/v1/search", headers=headers(seeded_user)).headers["Retry-After"] == "1"
        clock[0] += 0.5
        assert client.get("/api/v1/search", headers=headers(seeded_user)).status_code == 200


@pytest.mark.parametrize("path,body", [
    ("/api/v1/entries/ingest", {"transcript": "a new note"}),
    ("/api/v1/chat", {"question": "what did I study?"}),
])
def test_limits_prevent_model_calls_and_entry_mutations(limited_app, seeded_user, session, monkeypatch, path, body):
    app, _ = limited_app
    calls = []

    def unexpected_graph(*args):
        calls.append(args)
        raise AssertionError("limited requests must not build or invoke a model graph")

    monkeypatch.setattr("sra.api.routers.entries.build_ingestion_graph", unexpected_graph)
    monkeypatch.setattr("sra.api.routers.chat.build_query_graph", unexpected_graph)
    with TestClient(app) as client:
        for _ in range(2):
            assert client.get("/api/v1/search", headers=headers(seeded_user)).status_code == 200
        before = list(session.exec(select(StudyEntry)).all())
        assert client.post(path, headers=headers(seeded_user), json=body).status_code == 429
        assert calls == []
        assert list(session.exec(select(StudyEntry)).all()) == before


def test_anonymous_shared_routes_two_ips_and_untrusted_forwarding_headers(limited_app, seeded_user):
    app, _ = limited_app
    with TestClient(app, client=("192.0.2.1", 12000)) as first:
        assert first.get("/api/v1/auth/google/login", follow_redirects=False).status_code == 302
        assert first.post("/api/v1/auth/logout").status_code == 204
        for method, path, body in (
            ("GET", "/api/v1/auth/google/login", None),
            ("GET", "/api/v1/auth/google/callback?code=x&state=y", None),
            ("POST", "/api/v1/auth/desktop/exchange", {"completion_code": "x"}),
            ("POST", "/api/v1/auth/logout", None),
        ):
            response = first.request(method, path, json=body, headers={"X-Forwarded-For": "192.0.2.99"})
            assert response.status_code == 429
        assert first.get("/api/v1/search", headers=headers(seeded_user)).status_code == 200
        # No lifespan exit between clients: both observe the same application state.
        second = TestClient(app, client=("192.0.2.2", 12001))
        assert second.get("/api/v1/auth/google/login", follow_redirects=False).status_code == 302
        assert app.state.rate_limits.anonymous.summary().active_bucket_count == 2
        second.close()


def test_limited_callback_preserves_state_and_does_not_call_google(limited_app, monkeypatch):
    app, clock = limited_app
    calls = []

    def exchange(code, settings):
        calls.append(code)
        return GoogleIdentity("limited-callback", "callback@example.com", "Callback")

    monkeypatch.setattr("sra.api.routers.auth.exchange_google_code", exchange)
    with TestClient(app) as client:
        login = client.get("/api/v1/auth/google/login", follow_redirects=False)
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        client.post("/api/v1/auth/logout")
        params = {"state": state, "code": "one-use-code"}
        assert client.get("/api/v1/auth/google/callback", params=params).status_code == 429
        assert calls == []
        clock[0] += 2
        assert client.get("/api/v1/auth/google/callback", params=params, follow_redirects=False).status_code == 302
        assert calls == ["one-use-code"]


def test_admin_summary_redaction_rbac_and_shutdown_reset(limited_app, seeded_user, other_user, session):
    app, _ = limited_app
    other_user.role = UserRole.ADMIN
    session.add(other_user)
    session.commit()
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/rate-limits", headers=headers(seeded_user)).status_code == 403
        response = client.get("/api/v1/admin/rate-limits", headers=headers(other_user))
        assert response.status_code == 200
        assert response.json() == {
            "authenticated": {"capacity": 2, "refill_tokens_per_second": 0.5, "active_bucket_count": 1},
            "anonymous": {"capacity": 2, "refill_tokens_per_second": 0.5, "active_bucket_count": 0},
        }
        assert client.get("/api/v1/admin/usage", headers=headers(other_user)).status_code == 200
        assert client.get("/api/v1/admin/rate-limits", headers=headers(other_user)).status_code == 429
    assert app.state.rate_limits.authenticated.summary().active_bucket_count == 0


def test_app_factories_have_isolated_state(limited_app):
    first, _ = limited_app
    second = create_app()
    first.state.rate_limits.authenticated.consume("user:1")
    assert second.state.rate_limits.authenticated.summary().active_bucket_count == 0
