"""SRA-502 — APScheduler wiring: lifespan starts/stops the scheduler and the
two registered jobs run their wrapped callables when invoked."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.session import get_session
from sra.jobs import digest_scheduler

pytestmark = pytest.mark.phase5
SECRET = "rate-limit-test-signing-secret-with-entropy"


@pytest.fixture
def scheduler_app(monkeypatch, session):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_PREGEN_HOUR_UTC", "3")
    monkeypatch.setenv("SCHEDULER_WAKEUP_INTERVAL_S", "60")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield app
    get_settings.cache_clear()


def _headers(user):
    token = issue_app_credential(user.id, signing_secret=SECRET, ttl_seconds=300, now=datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}


def test_lifespan_starts_scheduler_when_enabled(scheduler_app, seeded_user):
    app = scheduler_app
    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "sra.pregenerate" in job_ids
        assert "sra.wakeup_tick" in job_ids


def test_lifespan_shuts_down_scheduler_on_exit(scheduler_app, seeded_user):
    app = scheduler_app
    with TestClient(app) as client:
        scheduler = app.state.scheduler
        assert scheduler is not None
        assert scheduler.running
        assert client.get("/api/health").status_code == 200

    assert app.state.scheduler is None or not scheduler.running


def test_pregeneration_job_invokes_pregenerate_for_today(scheduler_app, monkeypatch, session, seeded_user):
    captured: dict[str, object] = {}

    def fake_pregenerate(*, today, session):
        captured["today"] = today
        captured["session"] = session
        return {"users": 0, "entries_seen": 0, "sets_created": 0, "sets_skipped": 0, "errors": 0}

    import sra.jobs.quiz_pregeneration as quiz_pregen

    monkeypatch.setattr(quiz_pregen, "pregenerate_quizzes_for_day", fake_pregenerate)

    app = scheduler_app
    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        job = next(j for j in scheduler.get_jobs() if j.id == "sra.pregenerate")
        job.func()

    assert "today" in captured
    assert isinstance(captured["today"], type(__import__("datetime").date.today()))


def test_wakeup_job_invokes_run_wakeup_tick(scheduler_app, monkeypatch, session, seeded_user):
    captured: dict[str, object] = {}

    def fake_run(*, session, as_of_utc):
        captured["session"] = session
        captured["as_of_utc"] = as_of_utc
        return 0

    monkeypatch.setattr(digest_scheduler, "run_wakeup_tick", fake_run)

    app = scheduler_app
    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        job = next(j for j in scheduler.get_jobs() if j.id == "sra.wakeup_tick")
        job.func()

    assert "as_of_utc" in captured
    assert isinstance(captured["as_of_utc"], datetime)


def test_digest_ready_route_is_accessible_under_running_scheduler(scheduler_app, seeded_user):
    app = scheduler_app
    with TestClient(app) as client:
        response = client.get("/api/v1/digest/ready", headers=_headers(seeded_user))
        assert response.status_code == 200
        assert response.json() == {"ready": False}


def test_lifespan_noop_when_scheduler_disabled(monkeypatch, session):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        assert app.state.scheduler is None
        assert client.get("/api/health").status_code == 200
    assert app.state.scheduler is None
    get_settings.cache_clear()