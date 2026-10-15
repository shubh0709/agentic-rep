"""SRA-603 — APScheduler registers the demo reset interval only when ``DEMO_MODE=true``."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from sra.api.app import create_app
from sra.config import get_settings
from sra.db.session import get_session

SECRET = "rate-limit-test-signing-secret-with-entropy"


@pytest.fixture
def scheduler_demo_app(monkeypatch, session):
    """An app instance whose ``scheduler_enabled=true`` and demo flag comes from env vars."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_WAKEUP_INTERVAL_S", "60")
    monkeypatch.setenv("SCHEDULER_PREGEN_HOUR_UTC", "3")
    monkeypatch.setenv("DEMO_DATA_RETENTION_DAYS", "7")
    # The conftest autouse fixture sets DEMO_MODE here per-parametrize below.
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield app
    get_settings.cache_clear()


def test_scheduler_registers_demo_reset_when_demo_mode_enabled(monkeypatch, session):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "sra.demo_reset" in job_ids


def test_scheduler_does_not_register_demo_reset_when_demo_mode_disabled(monkeypatch, session):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "sra.demo_reset" not in job_ids


def test_scheduler_demo_reset_job_invokes_reset_demo_data(monkeypatch, session, seeded_user):
    """When the demo reset interval fires, the job calls ``reset_demo_data`` exactly once."""
    import sra.jobs.digest_scheduler as _ds

    captured: dict[str, object] = {}

    def fake_reset_demo_data(retention_days, *, as_of_utc, session_factory=None):
        captured["retention_days"] = retention_days
        captured["as_of_utc"] = as_of_utc
        captured["session_factory"] = session_factory
        return {"deleted": 0}

    import sra.jobs.demo_reset as _demo_reset

    monkeypatch.setattr(_demo_reset, "reset_demo_data", fake_reset_demo_data)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_DATA_RETENTION_DAYS", "14")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        job = next(j for j in scheduler.get_jobs() if j.id == "sra.demo_reset")
        assert job is not None
        # Invoke the job directly; APScheduler's interval trigger would otherwise
        # be deferred past the test's wall-clock budget.
        job.func()

    assert captured["retention_days"] == 14
    assert "as_of_utc" in captured
    assert isinstance(captured["as_of_utc"], datetime)
    assert _ds is not None  # keep the import alive for pyright


def test_scheduler_demo_reset_interval_uses_configured_seconds(monkeypatch, session):
    """The interval trigger uses ``Settings.demo_reset_interval_s`` so the operator controls cadence."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_RESET_INTERVAL_S", "120")
    get_settings.cache_clear()
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        job = next(j for j in scheduler.get_jobs() if j.id == "sra.demo_reset")
        # APScheduler exposes the trigger interval in seconds
        trigger = job.trigger
        assert trigger.interval.total_seconds() == 120