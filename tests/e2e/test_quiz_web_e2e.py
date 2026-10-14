"""SRA-404 web-quiz E2E loop: pregenerate, start, submit, complete through real router."""

from datetime import date

import pytest
from sqlmodel import select
from sra.agents.quiz_graph import generate_and_persist_questions
from sra.jobs.quiz_pregeneration import pregenerate_quizzes_for_day

from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.db.models import QuizAttempt, StudyEntry, StudyEntryCreate

pytestmark = pytest.mark.phase4


class FakeChatModel:
    def __init__(self, drafts: list[MCQDraft] | None = None):
        self._drafts = drafts or []

    def with_structured_output(self, schema, **kwargs):
        assert schema is MCQDrafts
        return self

    def invoke(self, *args, **kwargs):
        return MCQDrafts(drafts=self._drafts)


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _patch_generate(monkeypatch, drafts: list[MCQDraft]):
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeChatModel(drafts=drafts),
    )


def test_quiz_web_loop_through_real_router(monkeypatch, client, session, seeded_user):
    _seed_entry(session, seeded_user.id)
    drafts = [
        MCQDraft(
            question_text=f"Q{i}",
            options=["a", "b", "c", "d"],
            correct_option_index=i % 4,
            explanation=f"why {i}",
        )
        for i in range(3)
    ]
    _patch_generate(monkeypatch, drafts=drafts)

    pregenerate_quizzes_for_day(today=date.today(), session=session)

    today = client.get("/api/v1/quiz/today")
    assert today.status_code == 200
    today_body = today.json()
    assert len(today_body) == 1
    quiz_set_id = today_body[0]["id"]
    assert all("correct_option_index" not in q for q in today_body[0]["questions"])

    start = client.post(
        f"/api/v1/quiz/{quiz_set_id}/attempts",
        json={"mode": "web"},
    )
    assert start.status_code == 201
    attempt_id = start.json()["attempt_id"]

    detail = client.get(f"/api/v1/quiz/{quiz_set_id}")
    assert detail.status_code == 200
    detail_questions = detail.json()["questions"]
    for question in detail_questions:
        assert "correct_option_index" not in question

    correct_answers = 0
    for question in detail_questions:
        response = client.post(
            f"/api/v1/quiz/attempts/{attempt_id}/answers",
            json={
                "question_id": question["id"],
                "selected_option_index": next(
                    i for i, draft in enumerate(drafts)
                    if draft.question_text == question["question_text"]
                ),
            },
        )
        assert response.status_code == 200
        body = response.json()
        if body["is_correct"]:
            correct_answers += 1
        assert body["correct_option_index"] in range(0, 4)

    complete = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete")
    assert complete.status_code == 200
    body = complete.json()
    assert body["score"] == correct_answers
    assert body["total_questions"] == len(detail_questions)

    summary = client.get(f"/api/v1/quiz/attempts/{attempt_id}")
    assert summary.status_code == 200
    summary_body = summary.json()
    assert summary_body["id"] == attempt_id
    assert summary_body["score"] == correct_answers
    assert summary_body["completed_at"] is not None


def test_cross_user_isolation_through_real_router(monkeypatch, client, session, seeded_user, other_user):
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from sra.auth.google_oauth import issue_app_credential

    SECRET = "rate-limit-test-signing-secret-with-entropy"

    entry = _seed_entry(session, seeded_user.id)
    drafts = [
        MCQDraft(
            question_text="q", options=["a", "b", "c", "d"], correct_option_index=0, explanation="ok"
        )
    ]
    _patch_generate(monkeypatch, drafts=drafts)

    generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=1,
        option_count=4,
    )

    today = client.get("/api/v1/quiz/today")
    quiz_set_id = today.json()[0]["id"]

    other_token = issue_app_credential(
        other_user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    other_client = TestClient(client.app, headers={"Authorization": f"Bearer {other_token}"})

    assert other_client.get(f"/api/v1/quiz/{quiz_set_id}").status_code == 403
    assert other_client.get("/api/v1/quiz/today").status_code == 200
    today_other = other_client.get("/api/v1/quiz/today").json()
    assert today_other == []

    start = other_client.post(
        f"/api/v1/quiz/{quiz_set_id}/attempts",
        json={"mode": "web"},
    )
    assert start.status_code == 403

    attempts_other = session.exec(select(QuizAttempt).where(QuizAttempt.user_id == other_user.id)).all()
    assert attempts_other == []