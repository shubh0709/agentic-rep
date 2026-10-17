"""SRA-406 Phase 4 end-to-end integration test through the real router and SQLite."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.db.models import QuizAttempt, QuizSet, ReviewSchedule, StudyEntry, StudyEntryCreate
from sra.jobs.quiz_pregeneration import pregenerate_quizzes_for_day

pytestmark = pytest.mark.phase4


class FakeChatModel:
    def __init__(self, drafts: list[MCQDraft]):
        self._drafts = drafts

    def with_structured_output(self, schema, **kwargs):
        assert schema is MCQDrafts
        return self

    def invoke(self, *args, **kwargs):
        return MCQDrafts(drafts=self._drafts)


def _seed_entry(session, user_id: int, *, topic: str = "Photosynthesis") -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic=topic, summary="Light + dark reactions", raw_transcript="t"),
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
            scheduled_date=date.today(),
            status="pending",
        )
    )
    session.commit()
    return entry


def _patch_generate(monkeypatch, drafts: list[MCQDraft]):
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeChatModel(drafts),
    )


def test_phase4_end_to_end_completes_a_full_web_quiz(monkeypatch, client, session, seeded_user, other_user):
    _seed_entry(session, seeded_user.id, topic="Photosynthesis")
    _seed_entry(session, seeded_user.id, topic="Mitosis")
    _patch_generate(
        monkeypatch,
        drafts=[
            MCQDraft(
                question_text=f"q{i}",
                options=["a", "b", "c", "d"],
                correct_option_index=i % 4,
                explanation=f"e{i}",
            )
            for i in range(3)
        ],
    )

    pregenerate_quizzes_for_day(today=date.today(), session=session)

    today_payload = client.get("/api/v1/quiz/today").json()
    assert len(today_payload) == 2
    user_sets = session.exec(select(QuizSet).where(QuizSet.user_id == seeded_user.id)).all()
    assert len(user_sets) == 2

    quiz_set_id = today_payload[0]["id"]
    detail = client.get(f"/api/v1/quiz/{quiz_set_id}").json()
    questions = detail["questions"]
    assert all("correct_option_index" not in q for q in questions)

    start = client.post(
        f"/api/v1/quiz/{quiz_set_id}/attempts",
        json={"mode": "web"},
    )
    assert start.status_code == 201
    attempt_id = start.json()["attempt_id"]

    correct_count = 0
    for question in questions:
        response = client.post(
            f"/api/v1/quiz/attempts/{attempt_id}/answers",
            json={"question_id": question["id"], "selected_option_index": 0},
        )
        body = response.json()
        if body["is_correct"]:
            correct_count += 1
        assert body["correct_option_index"] in range(0, 4)

    completion = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete")
    assert completion.status_code == 200
    body = completion.json()
    assert body["score"] == correct_count
    assert body["total_questions"] == len(questions)

    summary = client.get(f"/api/v1/quiz/attempts/{attempt_id}").json()
    assert summary["score"] == correct_count


def test_phase4_end_to_end_pregeneration_is_idempotent(monkeypatch, client, session, seeded_user):
    _seed_entry(session, seeded_user.id)
    _patch_generate(
        monkeypatch,
        drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")],
    )

    first = pregenerate_quizzes_for_day(today=date.today(), session=session)
    second = pregenerate_quizzes_for_day(today=date.today(), session=session)

    assert first["sets_created"] >= 1
    assert second["sets_created"] == 0


def test_phase4_end_to_end_cross_user_isolation(monkeypatch, client, session, seeded_user, other_user):
    from datetime import UTC, datetime

    from sra.auth.google_oauth import issue_app_credential

    SECRET = "rate-limit-test-signing-secret-with-entropy"
    _seed_entry(session, seeded_user.id)
    _patch_generate(
        monkeypatch,
        drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")],
    )
    pregenerate_quizzes_for_day(today=date.today(), session=session)

    today = client.get("/api/v1/quiz/today").json()
    assert len(today) == 1
    quiz_set_id = today[0]["id"]

    other_token = issue_app_credential(
        other_user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    other_client = TestClient(client.app, headers={"Authorization": f"Bearer {other_token}"})

    assert other_client.get("/api/v1/quiz/today").json() == []
    assert other_client.get(f"/api/v1/quiz/{quiz_set_id}").status_code == 403
    start = other_client.post(
        f"/api/v1/quiz/{quiz_set_id}/attempts",
        json={"mode": "web"},
    )
    assert start.status_code == 403
    other_attempts = session.exec(select(QuizAttempt).where(QuizAttempt.user_id == other_user.id)).all()
    assert other_attempts == []
