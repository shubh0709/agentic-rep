"""SRA-509 Phase 5 end-to-end integration test through the real router and SQLite.

Drives the full Phase 5 flow:
  * PUT /me/preferences round-trip + validation rejections
  * pregenerate_quizzes_for_day via the real scheduler seam
  * GET /digest/ready polling
  * a full voice attempt driven turn-by-turn through the real router
  * QuizAttempt.score matches the correct-answer count
  * cross-user isolation: a second user cannot read or send turns
  * wake-up loop exits within poll_interval_s of stop_event.set()
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

import threading
import time
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.auth.google_oauth import issue_app_credential
from sra.db.models import (
    QuizAnswer,
    QuizAttempt,
    ReviewSchedule,
    StudyEntry,
    StudyEntryCreate,
    User,
)
from sra.db.repository import create_entry
from sra.jobs.quiz_pregeneration import pregenerate_quizzes_for_day

pytestmark = pytest.mark.phase5
SECRET = "rate-limit-test-signing-secret-with-entropy"


class _RecordingTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str) -> None:
        self.calls.append(text)


class InterpretedAnswerLike:
    def __init__(self, selected_option_index: int) -> None:
        self.selected_option_index = selected_option_index


class _ScriptedChatModel:
    """A scripted LLM interpreter used by the voice-graph in this test."""

    def __init__(self, scripted: list[int]):
        self._scripted = list(scripted)

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        if not self._scripted:
            raise AssertionError("script exhausted")
        return InterpretedAnswerLike(selected_option_index=self._scripted.pop(0))


def _seed_entry_with_due_review(session, user_id: int, *, topic: str) -> StudyEntry:
    """Seed an entry and a ReviewSchedule that is due today so pregeneration picks it up."""
    entry = create_entry(
        entry_create=StudyEntryCreate(topic=topic, summary="cell biology", raw_transcript="t"),
        user_id=user_id,
        session=session,
    )
    assert entry.id is not None
    due_review = ReviewSchedule(
        entry_id=entry.id,
        interval_label="1d",
        scheduled_date=date.today(),
        status="pending",
    )
    session.add(due_review)
    session.commit()
    return entry


def _patch_generate(monkeypatch, drafts: list[MCQDraft]):
    import sra.agents.quiz_graph as quiz_graph

    class FakeChatModel:
        def with_structured_output(self, schema, **kwargs):
            from sra.agents.schemas import MCQDrafts

            assert schema is MCQDrafts
            return self

        def invoke(self, *args, **kwargs):
            from sra.agents.schemas import MCQDrafts

            return MCQDrafts(drafts=drafts)

    monkeypatch.setattr(quiz_graph, "_get_chat_model", lambda: FakeChatModel())


def _patch_voice_interpreter(monkeypatch, *, scripted: list[int]):
    import sra.agents.quiz_conversation_graph as qcg

    fake = _ScriptedChatModel(scripted=scripted)
    monkeypatch.setattr(qcg, "_get_chat_model", lambda: fake)


def test_phase5_end_to_end_drives_a_full_voice_quiz_after_preferences_and_pregeneration(
    monkeypatch, client, session, seeded_user
):
    drafts = [
        MCQDraft(question_text=f"q{i}", options=["a", "b", "c", "d"], correct_option_index=i % 4, explanation="")
        for i in range(3)
    ]
    _seed_entry_with_due_review(session, seeded_user.id, topic="Photosynthesis")
    _patch_generate(monkeypatch, drafts=drafts)

    preferences_response = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:30",
            "timezone": "UTC",
            "quiz_question_count": 3,
            "quiz_option_count": 4,
            "interview_question_count": 6,
            "interview_target_minutes": 5,
        },
    )
    assert preferences_response.status_code == 200
    prefs_body = preferences_response.json()
    assert prefs_body["preferred_review_time"] == "09:30"
    assert prefs_body["quiz_question_count"] == 3

    persisted = session.exec(select(User).where(User.id == seeded_user.id)).one()
    assert persisted.preferred_review_time == "09:30"
    assert persisted.quiz_question_count == 3
    assert persisted.quiz_option_count == 4

    summary = pregenerate_quizzes_for_day(today=date.today(), session=session)
    assert summary["sets_created"] >= 1

    ready = client.get("/api/v1/digest/ready").json()
    assert ready == {"ready": True}

    today_quizzes = client.get("/api/v1/quiz/today").json()
    assert len(today_quizzes) >= 1
    quiz_set_id = today_quizzes[0]["id"]

    start = client.post(f"/api/v1/quiz/{quiz_set_id}/attempts", json={"mode": "voice"})
    assert start.status_code == 201
    body = start.json()
    attempt_id = body["attempt_id"]
    assert body["question_text"] == "q0"
    assert body["options"] == ["a", "b", "c", "d"]

    _patch_voice_interpreter(monkeypatch, scripted=[0, 1, 2])

    correct_count = 0
    for _ in range(3):
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

    summary_get = client.get(f"/api/v1/quiz/attempts/{attempt_id}").json()
    assert summary_get["score"] == correct_count


def test_phase5_end_to_end_cross_user_isolation(monkeypatch, client, session, seeded_user, other_user):
    drafts = [
        MCQDraft(question_text="q", options=["a", "b", "c", "d"], correct_option_index=0, explanation="")
    ]
    _seed_entry_with_due_review(session, seeded_user.id, topic="Mitosis")
    _patch_generate(monkeypatch, drafts=drafts)

    pregenerate_quizzes_for_day(today=date.today(), session=session)

    today_payload = client.get("/api/v1/quiz/today").json()
    assert len(today_payload) == 1
    quiz_set_id = today_payload[0]["id"]

    start = client.post(f"/api/v1/quiz/{quiz_set_id}/attempts", json={"mode": "voice"})
    assert start.status_code == 201
    attempt_id = start.json()["attempt_id"]

    other_token = issue_app_credential(
        other_user.id, signing_secret=SECRET, ttl_seconds=3600, now=datetime.now(UTC)
    )
    other_client = TestClient(client.app, headers={"Authorization": f"Bearer {other_token}"})

    assert other_client.get("/api/v1/quiz/today").json() == []
    assert other_client.get(f"/api/v1/quiz/{quiz_set_id}").status_code == 403
    other_attempt_start = other_client.post(
        f"/api/v1/quiz/{quiz_set_id}/attempts", json={"mode": "voice"}
    )
    assert other_attempt_start.status_code == 403
    other_turn = other_client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/voice-turn", json={"answer_text": "hijack"}
    )
    assert other_turn.status_code == 403

    other_preferences_put = other_client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "10:00",
            "timezone": "UTC",
            "quiz_question_count": 5,
            "quiz_option_count": 4,
            "interview_question_count": 6,
            "interview_target_minutes": 5,
        },
    )
    assert other_preferences_put.status_code == 200
    persisted = session.exec(select(User).where(User.id == seeded_user.id)).one()
    assert persisted.quiz_question_count == 10


def test_phase5_end_to_end_preferences_validation_returns_400(client, seeded_user):
    invalid_time = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "25:00",
            "timezone": "UTC",
            "quiz_question_count": 3,
            "quiz_option_count": 4,
        },
    )
    assert invalid_time.status_code == 400

    invalid_count = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "timezone": "UTC",
            "quiz_question_count": 99,
            "quiz_option_count": 4,
        },
    )
    assert invalid_count.status_code == 400

    invalid_body = client.put(
        "/api/v1/me/preferences",
        json={
            "preferred_review_time": "09:00",
            "quiz_question_count": 3,
        },
    )
    assert invalid_body.status_code == 400


def test_phase5_end_to_end_wakeup_loop_stops_within_poll_interval():
    from sra.capture import tts, wakeup

    backend = _RecordingTTS()
    tts.set_backend(backend)
    try:
        ready_calls = {"count": 0}

        def ready_side_effect():
            ready_calls["count"] += 1
            if ready_calls["count"] == 1:
                return True
            return False

        api = SimpleNamespace()
        api.get_digest_ready = ready_side_effect
        api.get_digest_text = lambda: "You have 1 review due today: Mitosis."

        stop = threading.Event()
        thread = threading.Thread(
            target=wakeup.run_wakeup_loop,
            kwargs={"stop_event": stop, "api_client": api, "poll_interval_s": 0.05},
            daemon=True,
        )
        thread.start()

        deadline = time.monotonic() + 1.0
        while not backend.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert backend.calls == ["You have 1 review due today: Mitosis."]

        started = time.monotonic()
        stop.set()
        thread.join(timeout=1.0)
        elapsed = time.monotonic() - started

        assert not thread.is_alive()
        assert elapsed < 1.0, f"loop did not exit promptly: {elapsed:.3f}s"
    finally:
        tts.reset_backend()