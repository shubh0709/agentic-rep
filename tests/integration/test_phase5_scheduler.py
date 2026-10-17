"""SRA-502/SRA-907 — APScheduler lifecycle, jobs, and startup catch-up."""

from datetime import UTC, date, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.models import QuizSet, ReviewSchedule, StudyEntry, StudyEntryCreate
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


def test_startup_after_daily_hour_pregenerates_due_entry_once_across_restart(
    monkeypatch, session, seeded_user
):
    """SRA-907: lifespan startup queues real pregeneration without wall-clock sleep."""
    from sra.agents import interview_planner, quiz_graph
    from sra.jobs import quiz_pregeneration

    entry = StudyEntry.model_validate(
        StudyEntryCreate(
            topic="Startup catch-up",
            summary="A due entry should receive its quiz when the API starts.",
            raw_transcript="The API was offline at the normal daily quiz hour.",
        ),
        update={"user_id": seeded_user.id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    session.add(
        ReviewSchedule(
            entry_id=entry.id,
            interval_label="1d",
            scheduled_date=date.today(),
            status="pending",
        )
    )
    session.commit()

    class FakeQuizModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return MCQDrafts(
                drafts=[
                    MCQDraft(
                        question_text="When should catch-up generation run?",
                        options=[
                            "At API startup",
                            "Only next week",
                            "After a browser refresh",
                            "After manual database repair",
                        ],
                        correct_option_index=0,
                        explanation="Startup closes the missed daily-run gap.",
                    )
                ]
            )

    class FakeInterviewModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return interview_planner.InterviewOutline(
                items=[
                    interview_planner.InterviewOutlineItem(
                        subtopic="startup",
                        difficulty="medium",
                        focus_prompt="Explain the scheduler catch-up behavior.",
                    )
                ]
            )

    monkeypatch.setattr(quiz_graph, "_get_chat_model", lambda: FakeQuizModel())
    monkeypatch.setattr(
        interview_planner, "_get_chat_model", lambda: FakeInterviewModel()
    )

    completed = Event()
    real_pregenerate = quiz_pregeneration.pregenerate_quizzes_for_day

    def observed_pregenerate(*, today, session):
        try:
            return real_pregenerate(today=today, session=session)
        finally:
            completed.set()

    monkeypatch.setattr(
        quiz_pregeneration, "pregenerate_quizzes_for_day", observed_pregenerate
    )

    engine = session.get_bind()
    monkeypatch.setattr(
        digest_scheduler, "_session_factory", lambda: Session(engine)
    )
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "false")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    most_recent_daily_hour = (datetime.now(UTC) - timedelta(hours=1)).hour
    monkeypatch.setenv(
        "SCHEDULER_PREGEN_HOUR_UTC", str(most_recent_daily_hour)
    )
    monkeypatch.setenv("SCHEDULER_WAKEUP_INTERVAL_S", "3600")
    get_settings.cache_clear()

    def start_once() -> None:
        app = create_app()
        app.dependency_overrides[get_session] = lambda: session
        with TestClient(app):
            assert completed.wait(timeout=2), (
                "API startup did not queue the pregeneration catch-up run"
            )

    start_once()
    session.expire_all()
    first_sets = session.exec(select(QuizSet).where(QuizSet.entry_id == entry.id)).all()
    assert len(first_sets) == 1

    completed.clear()
    start_once()
    session.expire_all()
    restarted_sets = session.exec(
        select(QuizSet).where(QuizSet.entry_id == entry.id)
    ).all()
    assert len(restarted_sets) == 1
    get_settings.cache_clear()
