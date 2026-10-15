from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import sra.db.models  # noqa: F401  (registers tables on SQLModel.metadata)
from sra.config import get_client_settings, get_settings


@pytest.fixture(autouse=True)
def disable_live_tracing(monkeypatch):
    """Keep tests offline even when the developer has enabled tracing locally."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    get_settings.cache_clear()
    get_client_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_client_settings.cache_clear()


@pytest.fixture
def session():
    """An isolated in-memory SQLite DB, fresh for every test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def seeded_user(session):
    from sra.db.models import User

    user = session.exec(select(User).where(User.google_sub == "test-subject")).first()
    if user is not None:
        return user
    user = User(email="test@example.com", display_name="Test User", google_sub="test-subject")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def other_user(session):
    from sra.db.models import User

    user = User(email="other@example.com", display_name="Other User", google_sub="other-subject")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def demo_user(session):
    """A demo-mode user with a full dependent subgraph.

    Creates one ``User`` row with ``is_demo_user=True`` plus one study
    entry, one review schedule, one quiz set, one quiz question, one
    quiz attempt, and one quiz answer — enough to assert the
    ``delete_user_cascade`` end-to-end clear and the
    ``POST /admin/demo/reset`` lifecycle.
    """
    from sra.agents.schemas import MCQDraft
    from sra.db.models import (
        QuizAnswer,
        QuizAttempt,
        QuizQuestion,
        StudyEntry,
        User,
    )
    from sra.db.repository import create_quiz_set, schedule_reviews_for_entry

    user = session.exec(select(User).where(User.google_sub == "demo-subject")).first()
    if user is not None:
        return user
    user = User(
        email="demo@example.com",
        display_name="Demo User",
        google_sub="demo-subject",
        is_demo_user=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None

    entry = StudyEntry(user_id=user.id, topic="DemoTopic", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    schedule_reviews_for_entry(entry.id, date.today(), session)

    drafts = [
        MCQDraft(
            question_text="What is 1+1?",
            options=["1", "2", "3", "4"],
            correct_option_index=1,
            explanation="Basic arithmetic.",
        )
    ]
    quiz_set = create_quiz_set(
        session=session,
        user_id=user.id,
        entry=entry,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )
    assert quiz_set.id is not None
    attempt = QuizAttempt(
        user_id=user.id,
        quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
        mode="web",
        total_questions=quiz_set.question_count,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    question = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).first()
    assert question is not None
    assert attempt.id is not None
    answer = QuizAnswer(
        attempt_id=attempt.id,
        question_id=question.id,
        selected_option_index=1,
        is_correct=True,
    )
    session.add(answer)
    session.commit()

    # Backdate the entry and attempt so the demo reset retention test
    # (`list_old_demo_users` cutoff at retention_days) finds the user.
    from sqlalchemy import text

    past = datetime.now(UTC) - timedelta(days=10)
    session.execute(
        text("UPDATE study_entries SET created_at = :ts WHERE id = :id"),
        {"ts": past.strftime("%Y-%m-%d %H:%M:%S"), "id": entry.id},
    )
    session.commit()
    return user


@pytest.fixture
def clock(monkeypatch):
    """A controllable datetime.now(UTC) for scheduler / wakeup tests.

    Phase 5 uses this to drive `users_due_for_wakeup` and the rate-limit /
    scheduler clock without depending on real wall time. The fixture patches
    `sra.db.models.datetime` (the same seam `tests/e2e/conftest.py::capture_api`
    uses) so SQLModel timestamps also read from this controlled clock.
    """
    from datetime import UTC, datetime

    from sra.db import models

    fixed = [datetime(2026, 10, 14, 9, 0, tzinfo=UTC)]

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed[0].replace(tzinfo=None)
            return fixed[0].astimezone(tz)

    monkeypatch.setattr(models, "datetime", FixedClock)

    def advance(seconds: int = 0, *, to: datetime | None = None) -> datetime:
        if to is not None:
            fixed[0] = to
        else:
            from datetime import timedelta

            fixed[0] = fixed[0] + timedelta(seconds=seconds)
        return fixed[0]

    yield advance


@pytest.fixture
def client(monkeypatch, session):
    """
    A TestClient for create_app(), with the DB dependency overridden
    to the shared in-memory `session` fixture (so requests and test
    assertions see the same data) and table creation short-circuited
    (the `session` fixture already created them against its own engine).
    """
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from sra.api.app import create_app
    from sra.auth.google_oauth import issue_app_credential
    from sra.config import get_settings
    from sra.db.models import User
    from sra.db.session import get_session

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "rate-limit-test-signing-secret-with-entropy")
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    get_settings.cache_clear()

    user = session.exec(select(User).where(User.google_sub == "test-subject")).first()
    if user is None:
        user = User(email="test@example.com", display_name="Test User", google_sub="test-subject")
        session.add(user)
        session.commit()
        session.refresh(user)
    assert user.id is not None
    credential = issue_app_credential(
        user.id,
        signing_secret="rate-limit-test-signing-secret-with-entropy",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, headers={"Authorization": f"Bearer {credential}"})
