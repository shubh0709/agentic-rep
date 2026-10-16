"""SRA-808 — Voice interview driver end-to-end with real create_app() + patched LLM."""

# pyright: reportAttributeAccessIssue=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import date

import pytest

from sra.agents.schemas import MCQDraft
from sra.db.models import StudyEntry, StudyEntryCreate

pytestmark = pytest.mark.phase8


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int) -> int:
    from sra.db.repository import create_quiz_set

    entry = _seed_entry(session, user_id)
    drafts = [
        MCQDraft(
            question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""
        )
    ]
    qs = create_quiz_set(
        entry=entry,
        user_id=user_id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=2,
        drafts=drafts,
    )
    return qs.id if qs.id is not None else 0


def _patch_interview_planner(monkeypatch, n: int = 1):
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


def test_phase8_voice_interview_e2e_drives_full_lifecycle(monkeypatch, client, session, seeded_user):
    """Run start -> 2 answers -> end through the real server with stubbed LLM.

    The voice client itself isn't invoked (no TTS / microphone in CI).
    Instead, the server's interview endpoints are exercised end-to-end so
    the entire HTTP shape — PlanOut, TurnOut, AttemptOut — round-trips
    against the same SQLModel metadata the rest of the app uses.
    """
    from sra.capture.voice_quiz_client import run_interview


    quiz_set_id = _make_quiz_set(session, seeded_user.id)
    _patch_interview_planner(monkeypatch)

    # Use the test client's underlying HTTP transport via TestClient for end-to-end
    # but bind run_interview to it through an api_client wrapper

    class _ApiClientBridge:
        def __init__(self, test_client):
            self.test_client = test_client

        def start_interview_attempt(self, quiz_set_id, *args, **kwargs):
            r = self.test_client.post(
                f"/api/v1/quiz/{quiz_set_id}/attempts",
                json={"mode": "interview"},
            )
            assert r.status_code == 201, r.text
            return r.json()

        def send_interview_turn(self, attempt_id, action, answer_text=None, **kwargs):
            body = {"action": action}
            if answer_text is not None:
                body["answer_text"] = answer_text
            r = self.test_client.post(
                f"/api/v1/quiz/attempts/{attempt_id}/interview-turn", json=body
            )
            assert r.status_code == 200, r.text
            return r.json()

        def get_interview(self, attempt_id, **kwargs):
            r = self.test_client.get(f"/api/v1/quiz/attempts/{attempt_id}/interview")
            assert r.status_code == 200, r.text
            return r.json()

    decisions = [
        dict(
            score=70,
            rationale="ok",
            feedback="good",
            next_action="ask_new",
            next_question_text="q2",
            finished=False,
            summary=None,
            transcript_so_far=[],
        ),
        dict(
            score=80,
            rationale="ok2",
            feedback="good2",
            next_action="finish",
            next_question_text=None,
            finished=True,
            summary="two answers logged",
            transcript_so_far=[],
        ),
    ]
    _patch_decision(monkeypatch, decisions)

    bridge = _ApiClientBridge(client)
    recorder_calls: list[str] = []
    transcriber_calls: list[str] = []

    def fake_record():
        recorder_calls.append("recorded")
        return "/tmp/x.wav"

    def fake_transcribe(path):
        transcriber_calls.append(path)
        return "the user said something"

    result = run_interview(
        quiz_set_id=quiz_set_id,
        api_client=bridge,
        recorder=fake_record,
        transcriber=fake_transcribe,
    )

    assert result["score"] == 80
    assert result["answered_count"] == 2
    assert len(recorder_calls) == 2
    assert len(transcriber_calls) == 2
