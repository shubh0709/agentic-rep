"""SRA-505 e2e — Voice quiz attempt through the real router + repository + SQLite."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.auth.google_oauth import issue_app_credential
from sra.db.models import (
    QuizAnswer,
    QuizAttempt,
    StudyEntry,
    StudyEntryCreate,
)
from sra.db.repository import create_quiz_set

pytestmark = pytest.mark.phase5

SECRET = "rate-limit-test-signing-secret-with-entropy"


@pytest.fixture
def quiz_conversation_graph():
    from sra.agents import quiz_conversation_graph as _qcg

    return _qcg


class _ScriptedChatModel:
    """A scripted LLM interpreter used by the voice-graph in this e2e test."""

    def __init__(self, scripted: list[int]):
        self._scripted = list(scripted)

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        if not self._scripted:
            raise AssertionError("script exhausted")
        return quiz_conversation_graph.InterpretedAnswer(
            selected_option_index=self._scripted.pop(0)
        )


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int, drafts: list[MCQDraft]) -> int:
    entry = _seed_entry(session, user_id)
    quiz_set = create_quiz_set(
        entry=entry,
        user_id=user_id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )
    assert quiz_set.id is not None
    return quiz_set.id


def _patch_voice_interpreter(monkeypatch, *, scripted: list[int], quiz_conversation_graph):
    fake = _ScriptedChatModel(scripted=scripted)
    monkeypatch.setattr(
        quiz_conversation_graph, "_get_chat_model",
        lambda: fake,
    )


def test_phase5_voice_quiz_e2e_completes_with_correct_score(
    monkeypatch, client, session, seeded_user, quiz_conversation_graph
):
    drafts = [
        MCQDraft(question_text="q0", options=["a", "b", "c", "d"], correct_option_index=0, explanation="ok"),
        MCQDraft(question_text="q1", options=["a", "b", "c", "d"], correct_option_index=1, explanation="ok"),
        MCQDraft(question_text="q2", options=["a", "b", "c", "d"], correct_option_index=2, explanation="ok"),
    ]
    quiz_set_id = _make_quiz_set(session, seeded_user.id, drafts)

    start = client.post(f"/api/v1/quiz/{quiz_set_id}/attempts", json={"mode": "voice"})
    assert start.status_code == 201
    body = start.json()
    attempt_id = body["attempt_id"]
    assert body["question_text"] == "q0"
    assert body["options"] == ["a", "b", "c", "d"]

    _patch_voice_interpreter(monkeypatch, scripted=[0, 1, 2], quiz_conversation_graph=quiz_conversation_graph)

    correct_count = 0
    for _draft in drafts:
        response = client.post(
            f"/api/v1/quiz/attempts/{attempt_id}/voice-turn",
            json={"answer_text": "answer"},
        )
        assert response.status_code == 200
        payload = response.json()
        if payload["is_correct"]:
            correct_count += 1
        if payload["finished"]:
            assert payload["score"] == correct_count
            assert payload["next_question_text"] is None
        else:
            assert payload["next_question_text"] is not None

    attempt = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt_id)).one()
    assert attempt.score == correct_count == 3
    assert attempt.completed_at is not None

    answers = session.exec(select(QuizAnswer).where(QuizAnswer.attempt_id == attempt_id)).all()
    assert len(answers) == 3


def test_phase5_voice_quiz_e2e_cross_user_returns_403(
    monkeypatch, client, session, seeded_user, other_user
):
    drafts = [MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")]
    quiz_set_id = _make_quiz_set(session, seeded_user.id, drafts)

    start = client.post(f"/api/v1/quiz/{quiz_set_id}/attempts", json={"mode": "voice"})
    attempt_id = start.json()["attempt_id"]

    other_token = issue_app_credential(
        other_user.id, signing_secret=SECRET, ttl_seconds=3600, now=datetime.now(UTC)
    )
    other_client = TestClient(client.app, headers={"Authorization": f"Bearer {other_token}"})

    voice = other_client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/voice-turn", json={"answer_text": "hijack"}
    )
    assert voice.status_code == 403


def test_phase5_voice_quiz_e2e_completed_attempt_returns_409(
    monkeypatch, client, session, seeded_user, quiz_conversation_graph
):
    drafts = [MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")]
    quiz_set_id = _make_quiz_set(session, seeded_user.id, drafts)

    start = client.post(f"/api/v1/quiz/{quiz_set_id}/attempts", json={"mode": "voice"})
    attempt_id = start.json()["attempt_id"]

    _patch_voice_interpreter(monkeypatch, scripted=[0], quiz_conversation_graph=quiz_conversation_graph)
    first = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/voice-turn", json={"answer_text": "yes"}
    )
    assert first.status_code == 200
    assert first.json()["finished"] is True

    _patch_voice_interpreter(monkeypatch, scripted=[0], quiz_conversation_graph=quiz_conversation_graph)
    second = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/voice-turn", json={"answer_text": "again"}
    )
    assert second.status_code == 409


def test_phase5_voice_quiz_e2e_voice_attempt_does_not_leak_correct_index(
    monkeypatch, client, session, seeded_user, quiz_conversation_graph
):
    """The voice response shape must never echo the canonical correct option index."""
    drafts = [
        MCQDraft(question_text="q0", options=["a", "b", "c", "d"], correct_option_index=0, explanation=""),
    ]
    quiz_set_id = _make_quiz_set(session, seeded_user.id, drafts)

    start = client.post(f"/api/v1/quiz/{quiz_set_id}/attempts", json={"mode": "voice"})
    assert start.status_code == 201
    body = start.json()
    assert "correct_option_index" not in body
    assert "correct_option_index" not in body["options"]

    attempt_id = body["attempt_id"]
    _patch_voice_interpreter(monkeypatch, scripted=[1], quiz_conversation_graph=quiz_conversation_graph)
    response = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/voice-turn", json={"answer_text": "the second one"}
    )
    payload = response.json()
    assert response.status_code == 200
    assert "correct_option_index" not in payload
    assert payload["is_correct"] is False
    assert payload["finished"] is True
