"""SRA-306 integration checks for refresh credential issuance, rotation, and reuse detection."""

from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import pytest

from sra.auth.google_oauth import GoogleIdentity
from sra.config import get_settings

pytestmark = pytest.mark.phase3

SECRET = "integration-auth-signing-secret-with-entropy"
ACCESS_COOKIE = "sra_session"
REFRESH_COOKIE = "sra_refresh"


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


def _complete_web_callback(auth_client, monkeypatch, *, subject="refresh-subject"):
    monkeypatch.setattr(
        "sra.api.routers.auth.exchange_google_code",
        lambda code, settings: GoogleIdentity(subject, f"{subject}@example.com", subject.title()),
    )
    login = auth_client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = auth_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "one-time-provider-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    return callback


def _cookie_pairs(set_cookie_header: str) -> list[dict[str, str]]:
    """Parse one or more comma-joined ``Set-Cookie`` header values.

    Starlette's TestClient (via httpx) returns a single ``set-cookie`` header
    whose value is the comma-joined concatenation of every cookie. Use the
    standard library's SimpleCookie loader so flag attributes (HttpOnly, Path,
    SameSite, Max-Age, ...) survive the round trip.
    """
    if not set_cookie_header:
        return []
    cookies = SimpleCookie()
    cookies.load(set_cookie_header)
    pairs: list[dict[str, str]] = []
    for name, morsel in cookies.items():
        pair: dict[str, str] = {"name": name, "value": morsel.value}
        for key, value in morsel.items():
            pair[key.lower()] = value.rstrip(",") if isinstance(value, str) else value
        pairs.append(pair)
    return pairs


def test_callback_sets_access_and_refresh_cookies_with_matching_flags(auth_client, monkeypatch):
    callback = _complete_web_callback(auth_client, monkeypatch)

    cookies = _cookie_pairs(callback.headers["set-cookie"])
    by_name = {c["name"]: c for c in cookies}

    assert ACCESS_COOKIE in by_name
    assert REFRESH_COOKIE in by_name
    assert by_name[ACCESS_COOKIE]["httponly"]
    assert by_name[REFRESH_COOKIE]["httponly"]
    assert by_name[REFRESH_COOKIE]["path"] == by_name[ACCESS_COOKIE]["path"]
    assert by_name[REFRESH_COOKIE]["samesite"] == by_name[ACCESS_COOKIE]["samesite"]
    assert by_name[ACCESS_COOKIE]["value"] != by_name[REFRESH_COOKIE]["value"]
    assert auth_client.get("/api/v1/auth/me").status_code == 200


def test_desktop_exchange_returns_refresh_fields_in_json(auth_client, monkeypatch):
    monkeypatch.setattr(
        "sra.api.routers.auth.exchange_google_code",
        lambda code, settings: GoogleIdentity("desktop-refresh", "desktop@example.com", "Desktop"),
    )
    login = auth_client.get(
        "/api/v1/auth/google/login", params={"client": "desktop", "port": 43123, "desktop_state": "x" * 32},
        follow_redirects=False,
    )
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = auth_client.get(
        "/api/v1/auth/google/callback",
        params={"code": "provider-code", "state": state},
        follow_redirects=False,
    )
    completion = parse_qs(urlsplit(callback.headers["location"]).query)["completion_code"][0]

    exchange = auth_client.post("/api/v1/auth/desktop/exchange", json={"completion_code": completion})

    assert exchange.status_code == 200
    body = exchange.json()
    assert set(body) >= {"credential", "refresh_credential", "expires_in", "refresh_expires_in"}
    assert body["refresh_credential"] != body["credential"]
    assert body["refresh_expires_in"] > body["expires_in"]


def test_refresh_route_rotates_jti_and_returns_new_pair_via_cookie(auth_client, monkeypatch):
    callback = _complete_web_callback(auth_client, monkeypatch)
    cookies = _cookie_pairs(callback.headers["set-cookie"])
    by_name = {c["name"]: c for c in cookies}
    original_refresh = by_name[REFRESH_COOKIE]["value"]
    original_access = by_name[ACCESS_COOKIE]["value"]

    refresh = auth_client.post("/api/v1/auth/refresh", follow_redirects=False)

    assert refresh.status_code == 200
    refreshed = _cookie_pairs(refresh.headers["set-cookie"])
    refreshed_by_name = {c["name"]: c for c in refreshed}
    assert REFRESH_COOKIE in refreshed_by_name
    assert ACCESS_COOKIE in refreshed_by_name
    assert refreshed_by_name[REFRESH_COOKIE]["value"] != original_refresh
    assert refreshed_by_name[ACCESS_COOKIE]["value"] != original_access

    assert auth_client.get("/api/v1/auth/me").status_code == 200


def test_refresh_route_replays_old_jti_are_rejected_and_revoke_whole_chain(auth_client, monkeypatch):
    callback = _complete_web_callback(auth_client, monkeypatch)
    refresh_value = next(
        c["value"] for c in _cookie_pairs(callback.headers["set-cookie"]) if c["name"] == REFRESH_COOKIE
    )

    auth_client.cookies.clear()
    auth_client.cookies.set(REFRESH_COOKIE, refresh_value)
    first = auth_client.post("/api/v1/auth/refresh", follow_redirects=False)
    assert first.status_code == 200
    second_refresh = next(
        c["value"] for c in _cookie_pairs(first.headers["set-cookie"]) if c["name"] == REFRESH_COOKIE
    )

    auth_client.cookies.clear()
    auth_client.cookies.set(REFRESH_COOKIE, second_refresh)
    third = auth_client.post("/api/v1/auth/refresh", follow_redirects=False)
    assert third.status_code == 200

    auth_client.cookies.clear()
    auth_client.cookies.set(REFRESH_COOKIE, refresh_value)
    replayed = auth_client.post("/api/v1/auth/refresh", follow_redirects=False)
    assert replayed.status_code == 401

    auth_client.cookies.clear()
    auth_client.cookies.set(REFRESH_COOKIE, second_refresh)
    revoked = auth_client.post("/api/v1/auth/refresh", follow_redirects=False)
    assert revoked.status_code == 401


def test_refresh_route_accepts_bearer_header(auth_client, monkeypatch):
    callback = _complete_web_callback(auth_client, monkeypatch)
    refresh_value = next(
        c["value"] for c in _cookie_pairs(callback.headers["set-cookie"]) if c["name"] == REFRESH_COOKIE
    )
    auth_client.cookies.clear()

    response = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_value}"},
        follow_redirects=False,
    )

    assert response.status_code == 200


def test_refresh_token_cannot_satisfy_require_user_on_study_route(auth_client, monkeypatch):
    callback = _complete_web_callback(auth_client, monkeypatch)
    refresh_value = next(
        c["value"] for c in _cookie_pairs(callback.headers["set-cookie"]) if c["name"] == REFRESH_COOKIE
    )
    auth_client.cookies.clear()

    response = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_value}"},
    )

    assert response.status_code == 401


def test_expired_access_with_valid_refresh_restores_study_access(auth_client, monkeypatch):
    callback = _complete_web_callback(auth_client, monkeypatch)
    cookie_pairs = _cookie_pairs(callback.headers["set-cookie"])
    refresh_value = next(c["value"] for c in cookie_pairs if c["name"] == REFRESH_COOKIE)

    auth_client.cookies.clear()
    auth_client.cookies.set(REFRESH_COOKIE, refresh_value)
    refreshed = auth_client.post("/api/v1/auth/refresh", follow_redirects=False)
    assert refreshed.status_code == 200

    response = auth_client.get("/api/v1/auth/me")

    assert response.status_code == 200


def test_logout_clears_both_access_and_refresh_cookies(auth_client, monkeypatch):
    _complete_web_callback(auth_client, monkeypatch)

    response = auth_client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    pairs = _cookie_pairs(set_cookie)
    names = {c["name"] for c in pairs}
    assert ACCESS_COOKIE in names
    assert REFRESH_COOKIE in names
    for cookie in pairs:
        assert cookie["max-age"] == "0" or cookie["value"] == ""
