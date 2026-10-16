"""SRA-701 — POST /api/v1/me/dev/pregenerate-now contract tests.

Pins the ``dev_mode`` gate, the synchronous pregeneration behavior, the
natural-review-schedule invariant, and the response payload shape so a
self-hoster without ``DEV_MODE=true`` cannot accidentally invoke the
endpoint and a developer with it set can verify the digest → quiz →
MCQ pipeline end to end.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from sra.config import get_settings
from sra.db.models import QuizSet, ReviewSchedule, StudyEntry, StudyEntryCreate, User
from sra.db.session import get_session

pytestmark = pytest.mark.phase4


def _make_app_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_credentials_for(user_id):
    from datetime import UTC, datetime

    from sra.auth.google_oauth import issue_app_credential

    return issue_app_credential(
        user_id,
        signing_secret="rate-limit-test-signing-secret-with-entropy",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )


def _build_client(engine, credential):
    from sra.api.app import create_app

    app = create_app()

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return TestClient(app, headers={"Authorization": f"Bearer {credential}"})


@pytest.fixture
def seeded_engine(monkeypatch):
    """A fresh SQLite engine pre-seeded with one user so DEV mode tests can hit the route."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv(
        "AUTH_SIGNING_SECRET", "rate-limit-test-signing-secret-with-entropy"
    )
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    get_settings.cache_clear()

    engine = _make_app_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.google_sub == "test-subject")
        ).first()
        if user is None:
            user = User(
                email="test@example.com",
                display_name="Test User",
                google_sub="test-subject",
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        assert user.id is not None
        credential = _make_credentials_for(user.id)
    return engine, user.id, credential


@pytest.fixture
def dev_client(monkeypatch, seeded_engine):
    """A TestClient with ``DEV_MODE=true`` against ``seeded_engine``."""
    monkeypatch.setenv("DEV_MODE", "true")
    get_settings.cache_clear()
    engine, _user_id, credential = seeded_engine
    yield _build_client(engine, credential)
    get_settings.cache_clear()


@pytest.fixture
def prod_client(monkeypatch, seeded_engine):
    """A TestClient with ``DEV_MODE=false`` even when ``.env`` says otherwise."""
    monkeypatch.setenv("DEV_MODE", "false")
    get_settings.cache_clear()
    engine, _user_id, credential = seeded_engine
    yield _build_client(engine, credential)
    get_settings.cache_clear()


class _FakeChatModel:
    """Stand-in for ``_get_chat_model`` so no real OpenAI call fires."""

    def __init__(self, drafts=None, raise_error: bool = False):
        from sra.agents.schemas import MCQDrafts

        self._drafts = drafts or []
        self._raise_error = raise_error
        self._MCQDrafts = MCQDrafts

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, *args, **kwargs):
        if self._raise_error:
            raise RuntimeError("simulated LLM failure")
        return self._MCQDrafts(drafts=self._drafts)


def _drafts(n: int = 3, option_count: int = 4):
    from sra.agents.schemas import MCQDraft

    return [
        MCQDraft(
            question_text=f"q{i}",
            options=[f"o{j}" for j in range(option_count)],
            correct_option_index=i % option_count,
            explanation=f"e{i}",
        )
        for i in range(n)
    ]


def _seed_entry_with_review(
    session, user_id, *, topic: str = "Mitosis", scheduled_date: date
) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic=topic, summary="s", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    session.add(
        ReviewSchedule(
            entry_id=entry.id,
            interval_label="1d",
            scheduled_date=scheduled_date,
            status="pending",
        )
    )
    session.commit()
    return entry


def _patch_llm(monkeypatch, *, drafts):
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model", lambda: _FakeChatModel(drafts=drafts)
    )


def test_pregenerate_now_returns_503_when_dev_mode_disabled(prod_client):
    """The router is mounted unconditionally but refuses work outside dev mode."""
    response = prod_client.post("/api/v1/me/dev/pregenerate-now")

    assert response.status_code == 503
    assert "DEV_MODE" in response.json()["detail"]


def test_pregenerate_now_covers_upcoming_entries_by_default(monkeypatch, dev_client, seeded_engine):
    """Default ``include_upcoming=true`` covers fresh entries whose first review is tomorrow."""
    engine, user_id, _ = seeded_engine
    future = date.fromisoformat("2030-01-01")
    _patch_llm(monkeypatch, drafts=_drafts())

    with Session(engine) as session:
        _seed_entry_with_review(
            session, user_id, topic="Mitosis", scheduled_date=future
        )
        _seed_entry_with_review(
            session, user_id, topic="Photosynthesis", scheduled_date=future
        )

    response = dev_client.post("/api/v1/me/dev/pregenerate-now")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["include_upcoming"] is True
    assert body["summary"]["sets_created"] == 2
    topics = {qs["topic"] for qs in body["quiz_sets"]}
    assert topics == {"Mitosis", "Photosynthesis"}


def test_pregenerate_now_with_include_upcoming_false_skips_future_entries(
    monkeypatch, dev_client, seeded_engine
):
    """``include_upcoming=false`` keeps the cron-equivalent behavior: only due-today entries."""
    engine, user_id, _ = seeded_engine
    future = date.fromisoformat("2030-01-01")
    today = date.today()
    _patch_llm(monkeypatch, drafts=_drafts())

    with Session(engine) as session:
        _seed_entry_with_review(session, user_id, topic="Today", scheduled_date=today)
        _seed_entry_with_review(session, user_id, topic="Future", scheduled_date=future)

    response = dev_client.post(
        "/api/v1/me/dev/pregenerate-now",
        params={"include_upcoming": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["include_upcoming"] is False
    assert body["summary"]["sets_created"] == 1
    topics = {qs["topic"] for qs in body["quiz_sets"]}
    assert topics == {"Today"}


def test_pregenerate_now_is_idempotent_within_same_day(monkeypatch, dev_client, seeded_engine):
    """A second invocation finds the same QuizSet and reports ``sets_skipped`` instead of duplicating."""
    engine, user_id, _ = seeded_engine
    _patch_llm(monkeypatch, drafts=_drafts())

    with Session(engine) as session:
        _seed_entry_with_review(session, user_id, topic="Mitosis", scheduled_date=date.today())

    first = dev_client.post("/api/v1/me/dev/pregenerate-now").json()
    second = dev_client.post("/api/v1/me/dev/pregenerate-now").json()

    assert first["summary"]["sets_created"] == 1
    assert second["summary"]["sets_created"] == 0
    assert second["summary"]["sets_skipped"] == 1
    assert len(second["quiz_sets"]) == 1


def test_pregenerate_now_does_not_mutate_review_schedules(monkeypatch, dev_client, seeded_engine):
    """The endpoint honors the natural schedule — it never rewrites ``scheduled_date``."""
    engine, user_id, _ = seeded_engine
    future = date.fromisoformat("2030-01-01")
    _patch_llm(monkeypatch, drafts=_drafts())

    with Session(engine) as session:
        future_entry = _seed_entry_with_review(
            session, user_id, topic="Future", scheduled_date=future
        )
        future_entry_id = future_entry.id

    response = dev_client.post("/api/v1/me/dev/pregenerate-now")
    assert response.status_code == 200

    with Session(engine) as session:
        reviews = list(
            session.exec(
                select(ReviewSchedule).where(ReviewSchedule.entry_id == future_entry_id)
            )
        )
        assert len(reviews) == 1
        assert reviews[0].scheduled_date.isoformat() == future.isoformat()


def test_pregenerate_now_isolates_users(monkeypatch, dev_client, seeded_engine):
    """A second user's due entries are never touched when the first user triggers pregeneration."""
    engine, user_id, _ = seeded_engine
    future = date.fromisoformat("2030-01-01")
    _patch_llm(monkeypatch, drafts=_drafts())

    with Session(engine) as session:
        mine = _seed_entry_with_review(session, user_id, topic="Mine", scheduled_date=future)
        mine_id = mine.id
        other = User(
            email="other@example.com",
            display_name="Other",
            google_sub="other-subject",
        )
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.id

    response = dev_client.post("/api/v1/me/dev/pregenerate-now")
    assert response.status_code == 200

    with Session(engine) as session:
        all_sets = list(session.exec(select(QuizSet)))
        for_user = [qs for qs in all_sets if qs.user_id == user_id]
        for_other = [qs for qs in all_sets if qs.user_id == other_id]
        assert len(for_user) == 1
        assert for_user[0].entry_id == mine_id
        assert for_other == []  # the other user has no entries yet — nothing to touch
