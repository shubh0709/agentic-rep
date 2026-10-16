"""SRA-805 — End-to-end interview lifecycle through the real router, repository, and SQLite."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import date

import pytest
from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.db.models import (
    QuizSet,
    StudyEntry,
    StudyEntryCreate,
)

# ``InterviewPlan`` / ``InterviewTurn`` are SRA-801 model symbols; import them
# inside test functions so a missing symbol fails that test, not module-level
# collection.

pytestmark = pytest.mark.phase8

SECRET = "rate-limit-test-signing-secret-with-entropy"


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int) -> QuizSet:
    from sra.db.repository import create_quiz_set

    entry = _seed_entry(session, user_id)
    drafts = [
        MCQDraft(
            question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""
        )
    ]
    return create_quiz_set(
        entry=entry,
        user_id=user_id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=2,
        drafts=drafts,
    )


def _patch_interview_planner(monkeypatch, n: int = 2):
    from sra.agents import interview_planner as _ip

    class _Plan:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            from sra.agents.interview_planner import (
                InterviewOutline,
                InterviewOutlineItem,
            )

            return InterviewOutline(
                items=[
                    InterviewOutlineItem(
                        subtopic=f"s{i}",
                        difficulty="medium",
                        focus_prompt=f"f{i}",
                    )
                    for i in range(n)
                ]
            )

    monkeypatch.setattr(_ip, "_get_chat_model", lambda: _Plan())


def _patch_decision(monkeypatch, scripted):
    from sra.agents import interview_conversation_graph as _icg

    class _FakeChatModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is _icg.InterviewDecision
            return self

        def invoke(self, messages, **kwargs):
            return _icg.InterviewDecision(**scripted.pop(0))

    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel())


def test_phase8_interview_e2e_full_lifecycle(monkeypatch, client, session, seeded_user):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    _patch_interview_planner(monkeypatch)

    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "interview"}
    )
    assert start.status_code == 201
    body = start.json()
    attempt_id = body["attempt_id"]
    assert body["question_text"] is not None
    assert isinstance(body["transcript_so_far"], list)
    assert body["transcript_so_far"][0]["role"] == "ai"

    decisions = [
        dict(
            score=70,
            rationale="ok",
            feedback="good",
            next_action="ask_new",
            next_question_text="q2",
        ),
        dict(
            score=80,
            rationale="ok2",
            feedback="good2",
            next_action="finish",
            next_question_text=None,
            summary="you answered two questions",
        ),
    ]
    _patch_decision(monkeypatch, decisions)

    first = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "a1"},
    )
    assert first.status_code == 200
    assert first.json()["score"] == 70

    advance = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "next"},
    )
    assert advance.status_code == 200

    second = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "a2"},
    )
    assert second.status_code == 200
    assert second.json()["finished"] is True
    assert second.json()["summary"] is not None

    detail = client.get(f"/api/v1/quiz/attempts/{attempt_id}/interview")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["plan"]["quiz_set_id"] == quiz_set.id
    roles = [t["role"] for t in detail_body["turns"]]
    assert roles.count("ai") >= 2
    assert "user" in roles
    assert "system_summary" in roles

    InterviewTurn = _import_interview_turn_cls()
    InterviewPlan = _import_interview_plan_cls()
    turns_in_db = session.exec(
        select(InterviewTurn).where(InterviewTurn.attempt_id == attempt_id)
    ).all()
    assert len(turns_in_db) >= 4
    summary_turns = [t for t in turns_in_db if t.role == "system_summary"]
    assert summary_turns
    plans = session.exec(select(InterviewPlan)).all()
    assert len(plans) == 1


def _import_interview_turn_cls():
    from sra.db.models import InterviewTurn
    return InterviewTurn


def _import_interview_plan_cls():
    from sra.db.models import InterviewPlan
    return InterviewPlan


def test_phase8_interview_e2e_wrong_mode_returns_400(
    monkeypatch, client, session, seeded_user
):
    """A web-mode attempt's interview-turn call must be rejected."""
    quiz_set = _make_quiz_set(session, seeded_user.id)
    start = client.post(f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "web"})
    attempt_id = start.json()["attempt_id"]

    response = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "x"},
    )
    assert response.status_code == 400


def test_phase8_interview_e2e_owns_attempt_returns_403(
    monkeypatch, client, session, seeded_user, other_user
):
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from sra.auth.google_oauth import issue_app_credential

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _patch_interview_planner(monkeypatch)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "interview"}
    )
    attempt_id = start.json()["attempt_id"]

    other_token = issue_app_credential(
        other_user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    other_client = TestClient(
        client.app, headers={"Authorization": f"Bearer {other_token}"}
    )

    response = other_client.get(f"/api/v1/quiz/attempts/{attempt_id}/interview")
    assert response.status_code == 403
