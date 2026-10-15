"""SRA-603 — admin on-demand reset endpoint integration tests.

Drives the FastAPI app through ``POST /api/v1/admin/demo/reset`` with all four
caller profiles (admin, non-admin, unauthenticated, demo-mode-disabled) and
verifies the documented HTTP status codes.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.models import StudyEntry, StudyEntryCreate, User, UserRole
from sra.db.session import get_session

SECRET = "rate-limit-test-signing-secret-with-entropy"


@pytest.fixture
def demo_admin_app(monkeypatch, session):
    """A FastAPI app configured with ``DEMO_MODE=true`` and a known admin user."""
    from sra.db import models

    # Build / find an admin user before any client request so the auth token can be minted.
    admin = session.exec(select(User).where(User.google_sub == "admin-sub")).first()
    if admin is None:
        admin = User(
            email="admin@example.com",
            display_name="Admin",
            google_sub="admin-sub",
            role=UserRole.ADMIN,
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
    non_admin = session.exec(select(User).where(User.google_sub == "non-admin-sub")).first()
    if non_admin is None:
        non_admin = User(
            email="non-admin@example.com",
            display_name="NonAdmin",
            google_sub="non-admin-sub",
            role=UserRole.USER,
        )
        session.add(non_admin)
        session.commit()
        session.refresh(non_admin)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_DATA_RETENTION_DAYS", "7")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    # The reset job reads Settings; ensure it sees the demo-mode overrides by patching
    # the lookup inside the demo_reset module.
    from sra.jobs import demo_reset as _demo_reset

    monkeypatch.setattr(_demo_reset, "get_settings", get_settings)
    # Suppress the clock edge for retention comparisons.
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield app, admin, non_admin, session, models
    get_settings.cache_clear()


def _bearer(user_id: int) -> dict[str, str]:
    token = issue_app_credential(
        user_id, signing_secret=SECRET, ttl_seconds=3600, now=datetime.now(UTC)
    )
    return {"Authorization": f"Bearer {token}"}


def _seed_demo_user_with_data(session, *, email: str, google_sub: str, is_demo_user: bool) -> int:
    user = User(email=email, display_name=email, google_sub=google_sub, is_demo_user=is_demo_user)
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="DemoTopic", summary="s", raw_transcript="t"),
        update={"user_id": user.id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    past = datetime.now(UTC) - timedelta(days=10)
    session.execute(
        text("UPDATE study_entries SET created_at = :ts WHERE id = :id"),
        {"ts": past.strftime("%Y-%m-%d %H:%M:%S"), "id": entry.id},
    )
    session.commit()
    return user.id


def test_admin_demo_reset_returns_deleted_count_for_demo_user(demo_admin_app):
    app, admin, _non_admin, session, _models = demo_admin_app
    demo_id = _seed_demo_user_with_data(
        session, email="demo@example.com", google_sub="demo-sub", is_demo_user=True
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/demo/reset", headers=_bearer(admin.id))

    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
    # The demo user is gone
    assert session.get(User, demo_id) is None


def test_admin_demo_reset_returns_403_for_non_admin(demo_admin_app):
    app, _admin, non_admin, session, _models = demo_admin_app

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/demo/reset", headers=_bearer(non_admin.id))

    assert response.status_code == 403


def test_admin_demo_reset_returns_401_when_unauthenticated(demo_admin_app):
    app, _admin, _non_admin, _session, _models = demo_admin_app

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/demo/reset")

    assert response.status_code == 401


def test_admin_demo_reset_returns_503_when_demo_mode_disabled(monkeypatch, session):
    """Outside demo mode the route is still mounted but the operation is refused."""
    from sra.db.models import UserRole

    admin = session.exec(select(User).where(User.google_sub == "admin-sub-503")).first()
    if admin is None:
        admin = User(
            email="admin503@example.com",
            display_name="Admin503",
            google_sub="admin-sub-503",
            role=UserRole.ADMIN,
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    from sra.jobs import demo_reset as _demo_reset

    monkeypatch.setattr(_demo_reset, "get_settings", get_settings)

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/demo/reset", headers=_bearer(admin.id))

    assert response.status_code == 503
    get_settings.cache_clear()


def test_admin_demo_reset_preserves_non_demo_users(demo_admin_app):
    """A non-demo user's dependents survive the reset."""
    app, admin, _non_admin, session, _models = demo_admin_app
    demo_id = _seed_demo_user_with_data(
        session, email="demo2@example.com", google_sub="demo-sub-2", is_demo_user=True
    )
    real_id = _seed_demo_user_with_data(
        session, email="real@example.com", google_sub="real-sub", is_demo_user=False
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/demo/reset", headers=_bearer(admin.id))

    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
    assert session.get(User, demo_id) is None
    assert session.get(User, real_id) is not None
    # The non-demo user's entry survives
    remaining = session.exec(select(StudyEntry).where(StudyEntry.user_id == real_id)).all()
    assert len(remaining) == 1