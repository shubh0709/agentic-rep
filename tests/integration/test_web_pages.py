"""Smoke tests for the browser-facing web module."""

from __future__ import annotations


def test_landing_renders_for_anonymous(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Spaced Repetition Agent" in body
    assert "Remember what you learn" in body


def test_signin_page_renders(monkeypatch, session):
    """The /signin page must render the Google CTA for an anonymous browser."""
    from fastapi.testclient import TestClient

    from sra.api.app import create_app
    from sra.config import get_settings
    from sra.db.session import get_session

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "web-smoke-signing-secret-with-entropy")
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    anon = TestClient(app)
    response = anon.get("/signin", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    assert "Sign in" in body
    # Either configured (Google button) or not configured (warning) — the page must work both ways.
    assert ("Continue with Google" in body) or ("Google OAuth is not configured" in body)


def test_quiz_page_requires_auth(monkeypatch, session):
    """Without a session cookie the quiz page must redirect / 401."""
    from fastapi.testclient import TestClient

    from sra.api.app import create_app
    from sra.db.session import get_session

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "web-smoke-signing-secret-with-entropy")
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    from sra.config import get_settings

    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    unauth = TestClient(app)
    response = unauth.get("/quiz")
    assert response.status_code == 401


def test_quiz_page_renders_for_authenticated_user(client):
    response = client.get("/quiz")
    assert response.status_code == 200
    body = response.text
    assert "Today's quiz" in body
    assert 'id="quiz-root"' in body
    assert "/static/js/quiz.js" in body


def test_settings_page_renders_with_current_values(client):
    response = client.get("/settings")
    assert response.status_code == 200
    body = response.text
    assert "Settings" in body
    assert 'id="preferences-form"' in body
    assert "preferred_review_time" in body
    assert "UTC" in body


def test_dashboard_page_requires_auth(monkeypatch, session):
    """Without a session cookie the dashboard page must 401."""
    from fastapi.testclient import TestClient

    from sra.api.app import create_app
    from sra.db.session import get_session

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "web-smoke-signing-secret-with-entropy")
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    from sra.config import get_settings

    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    unauth = TestClient(app)
    response = unauth.get("/dashboard")
    assert response.status_code == 401


def test_dashboard_page_renders_for_authenticated_user(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.text
    assert "Study dashboard" in body
    assert 'id="entries-root"' in body
    assert 'id="upcoming-root"' in body
    assert 'id="entry-search"' in body
    assert 'id="upcoming-window"' in body
    assert "/static/js/dashboard.js" in body


def test_dashboard_static_assets_are_served(client):
    """The dashboard module must be served from /static like every other page."""
    response = client.get("/static/js/dashboard.js")
    assert response.status_code == 200
    assert "/api/v1/search" in response.text
    assert "/api/v1/digest" in response.text
    assert "/api/v1/reviews/upcoming" in response.text


def test_authenticated_root_shows_dashboard_nav_link(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/dashboard"' in body


def test_signout_clears_cookie(client):
    response = client.post("/signout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    set_cookie = response.headers.get("set-cookie", "")
    assert "sra_session" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


def test_static_assets_are_served(client):
    css = client.get("/static/css/site.css")
    assert css.status_code == 200
    assert ":root" in css.text
    js = client.get("/static/js/quiz.js")
    assert js.status_code == 200
    assert "/api/v1/quiz" in js.text


def test_authenticated_root_shows_signed_in_nav(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Sign in" not in body  # the nav uses /signin only when anonymous
    assert "Sign out" in body
    assert "Test User" in body