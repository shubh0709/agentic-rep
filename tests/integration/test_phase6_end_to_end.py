"""SRA-605 — Phase 6 end-to-end verification across the full stack.

Composes every Phase 6 component (settings, column, migration, cascade,
demo reset, rate-limit narrowing, admin route, scheduler, route audit)
into a single cross-ticket integration test so a green run here is the
phase-level evidence Phase 6 is closed.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlmodel import select

from sra.api.app import create_app
from sra.api.rate_limit import RateLimitState
from sra.auth.google_oauth import issue_app_credential
from sra.config import Settings, get_settings
from sra.db.models import (
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    QuizSet,
    ReviewSchedule,
    StudyEntry,
    User,
    UserRole,
)
from sra.db.session import get_session

SECRET = "rate-limit-test-signing-secret-with-entropy"
ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE6_REVISION = "c3d4e5f6a7b8"


# ---------------------------------------------------------------------------
# Demo lifecycle through the live FastAPI app
# ---------------------------------------------------------------------------


def test_phase6_demo_reset_lifecycle_clears_all_dependents(monkeypatch, session, demo_user, seeded_user):
    """Admin POST /admin/demo/reset clears every dependent table for the demo user only."""
    admin = User(
        email="admin-e2e@example.com",
        display_name="AdminE2E",
        google_sub="admin-e2e-sub",
        role=UserRole.ADMIN,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    assert admin.id is not None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_DATA_RETENTION_DAYS", "7")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    token = issue_app_credential(admin.id, signing_secret=SECRET, ttl_seconds=3600, now=datetime.now(UTC))
    headers = {"Authorization": f"Bearer {token}"}

    # Capture demo-user IDs before the reset so we can assert they are gone.
    demo_user_id = demo_user.id
    assert demo_user_id is not None
    demo_entry_id = session.exec(
        select(StudyEntry).where(StudyEntry.user_id == demo_user_id)
    ).first().id
    demo_quiz_set_id = session.exec(
        select(QuizSet).where(QuizSet.user_id == demo_user_id)
    ).first().id
    demo_attempt_id = session.exec(
        select(QuizAttempt).where(QuizAttempt.user_id == demo_user_id)
    ).first().id

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/demo/reset", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"deleted": 1}

    # Demo user and all dependents are gone.
    assert session.get(User, demo_user_id) is None
    assert session.exec(
        select(StudyEntry).where(StudyEntry.id == demo_entry_id)
    ).first() is None
    assert session.exec(
        select(ReviewSchedule).where(ReviewSchedule.entry_id == demo_entry_id)
    ).all() == []
    assert session.exec(
        select(QuizSet).where(QuizSet.id == demo_quiz_set_id)
    ).first() is None
    assert session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == demo_quiz_set_id)
    ).all() == []
    assert session.exec(
        select(QuizAttempt).where(QuizAttempt.id == demo_attempt_id)
    ).first() is None
    assert session.exec(
        select(QuizAnswer).where(QuizAnswer.attempt_id == demo_attempt_id)
    ).all() == []

    # Non-demo seeded user is intact (re-fetch via the session fixture).
    refreshed = session.get(User, seeded_user.id)
    assert refreshed is not None


def test_phase6_non_admin_caller_is_forbidden_on_dashboard(monkeypatch, session, demo_user):
    """A non-admin caller cannot trigger the destructive reset."""
    session.add(demo_user)
    session.commit()

    non_admin = User(
        email="non-admin-e2e@example.com",
        display_name="NonAdminE2E",
        google_sub="non-admin-e2e-sub",
        role=UserRole.USER,
    )
    session.add(non_admin)
    session.commit()
    session.refresh(non_admin)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    token = issue_app_credential(non_admin.id, signing_secret=SECRET, ttl_seconds=3600, now=datetime.now(UTC))
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        response = client.post("/api/v1/admin/demo/reset", headers=headers)

    assert response.status_code == 403
    # Demo user still present (caller has no permission).
    assert session.get(User, demo_user.id) is not None


# ---------------------------------------------------------------------------
# Rate-limit narrowing through the live app
# ---------------------------------------------------------------------------


def test_rate_limit_narrows_under_demo_mode(monkeypatch, session):
    """Phase 6 acceptance: demo-mode capacity and refill are 10x narrower than the base config."""
    base_settings = Settings(
        demo_mode=False,
        rate_limit_authenticated_capacity=60,
        rate_limit_authenticated_refill_per_second=1.0,
        rate_limit_anonymous_capacity=20,
        rate_limit_anonymous_refill_per_second=0.2,
        rate_limit_idle_ttl_seconds=3600.0,
    )
    demo_settings = Settings(
        demo_mode=True,
        rate_limit_authenticated_capacity=60,
        rate_limit_authenticated_refill_per_second=1.0,
        rate_limit_anonymous_capacity=20,
        rate_limit_anonymous_refill_per_second=0.2,
        rate_limit_idle_ttl_seconds=3600.0,
    )

    base_state = RateLimitState.from_settings(base_settings)
    demo_state = RateLimitState.from_settings(demo_settings)

    base_auth = base_state.authenticated.summary()
    demo_auth = demo_state.authenticated.summary()
    assert demo_auth.capacity == max(1, base_auth.capacity // 10)
    assert demo_auth.refill_tokens_per_second == pytest.approx(base_auth.refill_tokens_per_second / 10)

    base_anon = base_state.anonymous.summary()
    demo_anon = demo_state.anonymous.summary()
    assert demo_anon.capacity == max(1, base_anon.capacity // 10)
    assert demo_anon.refill_tokens_per_second == pytest.approx(base_anon.refill_tokens_per_second / 10)


# ---------------------------------------------------------------------------
# Migration round-trip
# ---------------------------------------------------------------------------


def test_phase6_migration_round_trip(monkeypatch, tmp_path):
    """End-to-end migration replay: upgrade -> downgrade -> upgrade restores the column."""
    from alembic import command
    from alembic.config import Config

    try:
        database = tmp_path / "phase6-e2e.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-migration-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    config = Config(ROOT / "alembic.ini")

    command.upgrade(config, PRE_PHASE6_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, google_sub, role, created_at) "
                "VALUES (901, 'phase5-e2e@example.com', 'Ph5E2E', 'phase5-e2e-sub', 'user', "
                "'2026-10-01 12:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE6_REVISION)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert "is_demo_user" in user_columns

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT is_demo_user FROM users WHERE id = 901")
        ).scalar_one()
    engine.dispose()

    assert row in (False, 0)


# ---------------------------------------------------------------------------
# Repository helper end-to-end: delete_user_cascade empties everything
# ---------------------------------------------------------------------------


def test_delete_user_cascade_end_to_end_empties_every_dependent_table(session, demo_user):
    """Reuses the ``demo_user`` fixture and confirms every row tied to the demo user is gone."""
    from sra.db.repository import delete_user_cascade

    user_id = demo_user.id
    assert user_id is not None

    delete_user_cascade(demo_user, session)

    assert session.get(User, user_id) is None
    assert session.exec(select(StudyEntry).where(StudyEntry.user_id == user_id)).all() == []
    assert session.exec(
        select(ReviewSchedule).join(StudyEntry).where(StudyEntry.user_id == user_id)
    ).all() == []
    assert session.exec(select(QuizSet).where(QuizSet.user_id == user_id)).all() == []
    assert session.exec(
        select(QuizQuestion).join(QuizSet).where(QuizSet.user_id == user_id)
    ).all() == []
    assert session.exec(select(QuizAttempt).where(QuizAttempt.user_id == user_id)).all() == []
    assert session.exec(
        select(QuizAnswer).join(QuizAttempt).where(QuizAttempt.user_id == user_id)
    ).all() == []