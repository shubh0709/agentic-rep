"""SRA-406 web-quiz E2E through the real router, repository, and SQLite."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.agents.quiz_graph import generate_and_persist_questions
from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.auth.google_oauth import issue_app_credential
from sra.db.models import QuizAttempt, QuizSet, StudyEntry, StudyEntryCreate

pytestmark = pytest.mark.phase4

SECRET = "rate-limit-test-signing-secret-with-entropy"


class FakeChatModel:
    def __init__(self, drafts: list[MCQDraft]):
        self._drafts = drafts

    def with_structured_output(self, schema, **kwargs):
        assert schema is MCQDrafts
        return self

    def invoke(self, *args, **kwargs):
        return MCQDrafts(drafts=self._drafts)


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Photosynthesis", summary="light reactions", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _patch_generate(monkeypatch, drafts: list[MCQDraft]):
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeChatModel(drafts),
    )


def test_phase4_web_quiz_e2e_completes_loop(monkeypatch, client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    drafts = [
        MCQDraft(
            question_text="q0", options=["a", "b", "c", "d"], correct_option_index=0, explanation="ok"
        ),
        MCQDraft(
            question_text="q1", options=["w", "x", "y", "z"], correct_option_index=2, explanation="ok"
        ),
        MCQDraft(
            question_text="q2", options=["1", "2", "3", "4"], correct_option_index=1, explanation="ok"
        ),
    ]
    _patch_generate(monkeypatch, drafts)

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=3,
        option_count=4,
    )
    assert quiz_set.id is not None

    today = client.get("/api/v1/quiz/today").json()
    assert len(today) == 1
    assert today[0]["id"] == quiz_set.id
    for question in today[0]["questions"]:
        assert "correct_option_index" not in question

    detail = client.get(f"/api/v1/quiz/{quiz_set.id}").json()
    questions = detail["questions"]
    assert len(questions) == 3

    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    correct_count = 0
    for question, draft in zip(questions, drafts, strict=True):
        selected_index = draft.correct_option_index
        response = client.post(
            f"/api/v1/quiz/attempts/{attempt_id}/answers",
            json={"question_id": question["id"], "selected_option_index": selected_index},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["is_correct"] is True
        assert result["correct_option_index"] == selected_index
        correct_count += 1

    completion = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete").json()
    assert completion["score"] == correct_count
    assert completion["total_questions"] == len(questions)

    summary = client.get(f"/api/v1/quiz/attempts/{attempt_id}").json()
    assert summary["score"] == correct_count
    assert summary["completed_at"] is not None


def test_phase4_web_quiz_e2e_cross_user_returns_403(monkeypatch, client, session, seeded_user, other_user):
    entry = _seed_entry(session, seeded_user.id)
    drafts = [MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")]
    _patch_generate(monkeypatch, drafts)
    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=1,
        option_count=2,
    )

    other_token = issue_app_credential(
        other_user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    other_client = TestClient(client.app, headers={"Authorization": f"Bearer {other_token}"})

    assert other_client.get(f"/api/v1/quiz/{quiz_set.id}").status_code == 403
    start = other_client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    assert start.status_code == 403

    other_attempts = session.exec(select(QuizAttempt).where(QuizAttempt.user_id == other_user.id)).all()
    assert other_attempts == []


def test_phase4_web_quiz_e2e_returns_404_for_missing_quiz(client):
    response = client.get("/api/v1/quiz/9999999")
    assert response.status_code == 404


def test_phase4_web_quiz_e2e_returns_401_without_authentication(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=1,
        option_count=2,
    )

    anon = TestClient(client.app)
    response = anon.get(f"/api/v1/quiz/{quiz_set.id}")
    assert response.status_code == 401


def test_phase4_web_quiz_e2e_records_attempt_score(client, session, seeded_user, monkeypatch):
    entry = _seed_entry(session, seeded_user.id)
    drafts = [
        MCQDraft(question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""),
        MCQDraft(question_text="q1", options=["a", "b"], correct_option_index=1, explanation=""),
    ]
    _patch_generate(monkeypatch, drafts)
    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=2,
        option_count=2,
    )

    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    from sra.db.models import QuizQuestion

    questions = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).all()

    for q in questions:
        client.post(
            f"/api/v1/quiz/attempts/{attempt_id}/answers",
            json={"question_id": q.id, "selected_option_index": q.correct_option_index},
        )

    client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete")

    attempt = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt_id)).one()
    assert attempt.score == 2
    assert attempt.completed_at is not None

    set_rows = session.exec(select(QuizSet).where(QuizSet.id == quiz_set.id)).all()
    assert len(set_rows) == 1
    assert set_rows[0].generated_on == date.today()