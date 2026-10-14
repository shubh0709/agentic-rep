"""SRA-302 integration checks through real auth dependencies and repositories."""

from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlmodel import select

from sra.auth.google_oauth import GoogleIdentity, issue_app_credential
from sra.config import get_settings
from sra.db.models import ReviewSchedule, StudyEntry, User, UserRole

pytestmark = pytest.mark.phase3
SECRET = "integration-auth-signing-secret-with-entropy"


@pytest.fixture
def auth_client(client, monkeypatch):
    client.headers.pop("Authorization", None)
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "provider-client-secret")
    monkeypatch.setenv("GOOGLE_CALLBACK_URL", "http://127.0.0.1:8000/api/v1/auth/google/callback")
    get_settings.cache_clear()
    yield client
    get_settings.cache_clear()


def _credential(user: User) -> str:
    assert user.id is not None
    return issue_app_credential(user.id, signing_secret=SECRET, ttl_seconds=300, now=datetime.now(UTC))


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_credential(user)}"}


def test_cookie_bearer_missing_invalid_and_conflicting_transports(auth_client, seeded_user):
    credential = _credential(seeded_user)

    assert auth_client.get("/api/v1/auth/me").status_code == 401
    assert auth_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    bearer = auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {credential}"})
    assert bearer.status_code == 200
    assert set(bearer.json()) == {"subject", "email", "display_name", "role"}

    auth_client.cookies.set("sra_session", credential)
    assert auth_client.get("/api/v1/auth/me").status_code == 200
    conflict = auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {credential}"})
    assert conflict.status_code == 401


def test_two_users_have_disjoint_search_and_digest(auth_client, session, seeded_user, other_user):
    mine = StudyEntry(user_id=seeded_user.id, topic="Alpha marker", summary="mine", raw_transcript="alpha")
    theirs = StudyEntry(user_id=other_user.id, topic="Beta marker", summary="theirs", raw_transcript="beta")
    session.add_all([mine, theirs])
    session.commit()
    session.refresh(mine)
    session.refresh(theirs)
    assert mine.id is not None and theirs.id is not None
    session.add_all(
        [
            ReviewSchedule(entry_id=mine.id, interval_label="1d", scheduled_date=date.today()),
            ReviewSchedule(entry_id=theirs.id, interval_label="1d", scheduled_date=date.today()),
        ]
    )
    session.commit()

    mine_search = auth_client.get("/api/v1/search?q=", headers=_headers(seeded_user)).json()
    their_search = auth_client.get("/api/v1/search?q=", headers=_headers(other_user)).json()
    assert {row["id"] for row in mine_search} == {mine.id}
    assert {row["id"] for row in their_search} == {theirs.id}
    assert {row["entry"]["id"] for row in auth_client.get("/api/v1/digest", headers=_headers(seeded_user)).json()} == {
        mine.id
    }


def test_admin_usage_is_aggregate_only(auth_client, session, seeded_user, other_user):
    assert auth_client.get("/api/v1/admin/usage", headers=_headers(seeded_user)).status_code == 403
    other_user.role = UserRole.ADMIN
    session.add(other_user)
    session.commit()
    response = auth_client.get("/api/v1/admin/usage", headers=_headers(other_user))
    assert response.status_code == 200
    assert set(response.json()) == {"user_count", "entry_count", "entries_created_today"}
    assert not {"topic", "summary", "raw_transcript", "email", "google_sub"} & set(response.json())


def test_callback_validates_state_before_exchange_and_logout_clears_cookie(auth_client, monkeypatch):
    login = auth_client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    calls: list[str] = []

    def fake_exchange(code, settings):
        del settings
        calls.append(code)
        return GoogleIdentity("callback-subject", "callback@example.com", "Callback User")

    monkeypatch.setattr("sra.api.routers.auth.exchange_google_code", fake_exchange)
    invalid = auth_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "must-not-be-used", "state": f"{state}altered"},
        follow_redirects=False,
    )
    assert invalid.status_code == 400
    assert calls == []

    callback = auth_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "one-time-provider-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert calls == ["one-time-provider-code"]
    assert auth_client.get("/api/v1/auth/me").status_code == 200
    logout = auth_client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_desktop_callback_exposes_completion_code_not_bearer_and_rejects_replay(auth_client, monkeypatch):
    nonce = "desktop-attempt-binding-0123456789"
    login = auth_client.get(
        "/api/v1/auth/google/login", params={"client": "desktop", "port": 43123, "desktop_state": nonce},
        follow_redirects=False,
    )
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    monkeypatch.setattr(
        "sra.api.routers.auth.exchange_google_code",
        lambda code, settings: GoogleIdentity("desktop-subject", "desktop@example.com", "Desktop User"),
    )
    callback = auth_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "provider-code", "state": state},
        follow_redirects=False,
    )
    loopback = urlsplit(callback.headers["location"])
    query = parse_qs(loopback.query)
    assert (loopback.scheme, loopback.hostname, loopback.port) == ("http", "127.0.0.1", 43123)
    assert set(query) == {"completion_code", "state"}
    assert query["state"] == [nonce]

    completion = query["completion_code"][0]
    exchange = auth_client.post("/api/v1/auth/desktop/exchange", json={"completion_code": completion})
    assert exchange.status_code == 200
    credential = exchange.json()["credential"]
    assert credential not in callback.headers["location"]
    assert auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {credential}"}).status_code == 200
    assert auth_client.post("/api/v1/auth/desktop/exchange", json={"completion_code": completion}).status_code == 400


def test_repeat_callback_upsert_keeps_one_subject_row(auth_client, monkeypatch, session):
    monkeypatch.setattr(
        "sra.api.routers.auth.exchange_google_code",
        lambda code, settings: GoogleIdentity("repeat-subject", f"{code}@example.com", "Repeat User"),
    )
    for code in ("first", "second"):
        login = auth_client.get("/api/v1/auth/google/login", follow_redirects=False)
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        assert auth_client.get(
            "/api/v1/auth/google/callback",
            params={"code": code, "state": state},
            follow_redirects=False,
        ).status_code == 302
    rows = session.exec(select(User).where(User.google_sub == "repeat-subject")).all()
    assert len(rows) == 1
    assert rows[0].email == "second@example.com"


def test_web_login_requires_the_initiating_browser_cookie_and_recovers(auth_client, monkeypatch):
    calls = []

    def exchange(code, settings):
        calls.append(code)
        return GoogleIdentity("browser-bound", "browser@example.com", "Browser")

    monkeypatch.setattr("sra.api.routers.auth.exchange_google_code", exchange)
    login = auth_client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    auth_client.cookies.clear()
    rejected = auth_client.get(
        "/api/v1/auth/google/callback", params={"state": state, "code": "not-exchanged"}, follow_redirects=False,
    )
    assert rejected.status_code == 400
    assert calls == []
    login = auth_client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    accepted = auth_client.get(
        "/api/v1/auth/google/callback", params={"state": state, "code": "exchanged"}, follow_redirects=False,
    )
    assert accepted.status_code == 302
    assert calls == ["exchanged"]
    assert not any(cookie.name.endswith("_login") for cookie in auth_client.cookies.jar)
    assert auth_client.get("/api/v1/auth/me").json()["subject"] == "browser-bound"


def test_desktop_login_roundtrip_uses_real_loopback_and_reuses_stored_credential(auth_client, monkeypatch):
    import threading
    import urllib.request
    from types import SimpleNamespace

    import httpx

    from sra.client import auth_client as desktop

    values = {}
    backend = SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, value: values.__setitem__((service, account), value),
        delete_password=lambda service, account: values.pop((service, account)),
    )
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(
        "sra.api.routers.auth.exchange_google_code",
        lambda code, settings: GoogleIdentity("roundtrip", "roundtrip@example.com", "Roundtrip"),
    )
    clients = []

    def send(request):
        response = auth_client.request(request.method, str(request.url), content=request.content, headers=request.headers)
        return httpx.Response(response.status_code, content=response.content, headers=response.headers, request=request)

    def create_client():
        connection = httpx.Client(transport=httpx.MockTransport(send))
        clients.append(connection)
        return connection

    monkeypatch.setattr(desktop, "_create_http_client", create_client)
    threads = []
    outcomes = []

    def open_browser(url):
        def complete():
            try:
                login = auth_client.get(url, follow_redirects=False)
                state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
                callback = auth_client.get(
                    "/api/v1/auth/google/callback", params={"state": state, "code": "test-provider-code"},
                    follow_redirects=False,
                )
                with urllib.request.urlopen(callback.headers["location"], timeout=2) as response:
                    outcomes.append(response.status)
            except Exception:
                outcomes.append("failed")

        thread = threading.Thread(target=complete)
        threads.append(thread)
        thread.start()
        return True

    store = desktop.MacOSKeychainCredentialStore(backend=backend)
    try:
        account = desktop.sign_in(
            api_base_url="http://testserver", timeout_seconds=3, store=store, open_browser=open_browser,
        )
    finally:
        for thread in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()
    assert outcomes == [200]
    assert account.subject == "roundtrip"
    restored = desktop.load_default_authenticated_account(
        api_base_url="http://testserver", store=desktop.MacOSKeychainCredentialStore(backend=backend),
    )
    assert restored.credential == account.credential
    assert restored.subject == account.subject
    assert all(connection.is_closed for connection in clients)
