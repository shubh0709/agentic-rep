
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-404 quiz API routes, including dependency inversion of model and rate limit."""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.models import QuizAttempt, QuizSet, StudyEntry, StudyEntryCreate
from sra.db.repository import create_quiz_set

pytestmark = pytest.mark.phase4

SECRET = "rate-limit-test-signing-secret-with-entropy"


def _credentials_for(user):
    from datetime import UTC, datetime

    token = issue_app_credential(
        user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    return {"Authorization": f"Bearer {token}"}


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _seed_quiz_set(session, entry: StudyEntry, drafts: list[MCQDraft] | None = None) -> QuizSet:
    drafts = drafts or [
        MCQDraft(
            question_text="q0", options=["a", "b", "c", "d"], correct_option_index=0, explanation="ok"
        ),
        MCQDraft(
            question_text="q1", options=["a", "b", "c", "d"], correct_option_index=1, explanation="ok"
        ),
    ]
    return create_quiz_set(
        entry=entry,
        user_id=entry.user_id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )


def _patch_generate(monkeypatch, *, drafts: list[MCQDraft] | None = None, raise_error: bool = False):
    fake = type(
        "FakeModel",
        (),
        {
            "with_structured_output": lambda self, schema, **kw: self,
            "invoke": (
                MagicMock(side_effect=RuntimeError("simulated LLM failure"))
                if raise_error
                else MagicMock(return_value=MCQDrafts(drafts=drafts or _default_drafts()))
            ),
        },
    )()
    monkeypatch.setattr("sra.agents.quiz_graph._get_chat_model", lambda: fake)


def _default_drafts() -> list[MCQDraft]:
    return [
        MCQDraft(
            question_text="q0", options=["a", "b", "c", "d"], correct_option_index=0, explanation="ok"
        ),
    ]


def test_get_today_returns_only_authenticated_users_sets_with_correct_option_index_stripped(
    client, session, seeded_user, monkeypatch
):
    entry = _seed_entry(session, seeded_user.id)
    _seed_quiz_set(session, entry)

    response = client.get("/api/v1/quiz/today")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["entry"]["topic"] == "Mitosis"
    for question in body[0]["questions"]:
        assert "correct_option_index" not in question
        assert set(question) == {"id", "question_text", "options", "order_index"}


def test_get_quiz_set_returns_owner_payload(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    response = client.get(f"/api/v1/quiz/{quiz_set.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == quiz_set.id
    for question in body["questions"]:
        assert "correct_option_index" not in question


def test_get_quiz_set_returns_404_when_missing(client, seeded_user):
    response = client.get("/api/v1/quiz/9999999")
    assert response.status_code == 404


def test_get_quiz_set_returns_403_for_other_user(client, session, seeded_user, other_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    client_b = TestClient(client.app, headers=_credentials_for(other_user))
    response = client_b.get(f"/api/v1/quiz/{quiz_set.id}")

    assert response.status_code == 403


def test_get_quiz_set_returns_401_when_unauthenticated(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    anon = TestClient(client.app)
    response = anon.get(f"/api/v1/quiz/{quiz_set.id}")
    assert response.status_code == 401


def test_start_attempt_web_creates_quiz_attempt(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    response = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )

    assert response.status_code == 201
    body = response.json()
    assert "attempt_id" in body
    assert body["attempt_id"] is not None
    attempts = session.exec(select(QuizAttempt).where(QuizAttempt.quiz_set_id == quiz_set.id)).all()
    assert len(attempts) == 1
    assert attempts[0].mode == "web"


def test_start_attempt_voice_is_accepted_and_returns_first_question(client, session, seeded_user):
    """SRA-505 added voice mode support; voice attempts now return 201 with the first MCQ.

    The legacy 400 rejection no longer applies: ``StartAttemptRequest.mode``
    accepts ``web``, ``voice``, and ``interview`` so existing UI form selects
    continue to work after Phase 8 lands. Keep this regression guard so a
    future refactor cannot silently drop voice again.
    """
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    response = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "voice"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["attempt_id"] is not None
    questions = session.exec(
        select(__import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion).where(
            __import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion.quiz_set_id == quiz_set.id
        )
    ).all()
    assert body["question_text"] == questions[0].question_text
    attempts = session.exec(select(QuizAttempt).where(QuizAttempt.quiz_set_id == quiz_set.id)).all()
    assert len(attempts) == 1
    assert attempts[0].mode == "voice"


def test_start_attempt_rejects_unknown_mode_with_422_and_no_row(client, session, seeded_user):
    """The schema still rejects values outside the documented set."""
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    response = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "smell"},
    )

    assert response.status_code == 422
    attempts = session.exec(select(QuizAttempt).where(QuizAttempt.quiz_set_id == quiz_set.id)).all()
    assert attempts == []


def test_submit_answer_records_grade_returns_correct_option_index_and_explanation(
    client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    assert start.status_code == 201
    attempt_id = start.json()["attempt_id"]

    questions = session.exec(
        select(__import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion).where(
            __import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion.quiz_set_id == quiz_set.id
        )
    ).all()
    assert len(questions) == 2

    response = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/answers",
        json={"question_id": questions[0].id, "selected_option_index": 0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_correct"] is True
    assert body["correct_option_index"] == 0
    assert "explanation" in body


def test_submit_answer_rejects_voice_attempt_with_400(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    attempt = QuizAttempt(
        user_id=seeded_user.id,
        quiz_set_id=quiz_set.id,
        mode="voice",
        total_questions=quiz_set.question_count,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    response = client.post(
        f"/api/v1/quiz/attempts/{attempt.id}/answers",
        json={"question_id": 1, "selected_option_index": 0},
    )
    assert response.status_code == 400


def test_submit_answer_rejects_completed_attempt_with_409(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    questions = session.exec(
        select(__import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion).where(
            __import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion.quiz_set_id == quiz_set.id
        )
    ).all()

    first = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/answers",
        json={"question_id": questions[0].id, "selected_option_index": 0},
    )
    assert first.status_code == 200

    completion = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete")
    assert completion.status_code == 200

    second = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/answers",
        json={"question_id": questions[1].id, "selected_option_index": 1},
    )
    assert second.status_code == 409


def test_submit_answer_returns_404_for_missing_attempt(client, seeded_user):
    response = client.post(
        "/api/v1/quiz/attempts/9999999/answers",
        json={"question_id": 1, "selected_option_index": 0},
    )
    assert response.status_code == 404


def test_complete_attempt_returns_score_and_total_questions(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)

    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    questions = session.exec(
        select(__import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion).where(
            __import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion.quiz_set_id == quiz_set.id
        )
    ).all()
    for q in questions:
        response = client.post(
            f"/api/v1/quiz/attempts/{attempt_id}/answers",
            json={"question_id": q.id, "selected_option_index": q.correct_option_index},
        )
        assert response.status_code == 200

    completion = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete")

    assert completion.status_code == 200
    body = completion.json()
    assert body["score"] == len(questions)
    assert body["total_questions"] == len(questions)


def test_complete_attempt_returns_409_when_already_completed(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    first = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete")
    assert first.status_code == 200

    second = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete")
    assert second.status_code == 409


def test_complete_attempt_returns_400_for_voice_mode(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    attempt = QuizAttempt(
        user_id=seeded_user.id,
        quiz_set_id=quiz_set.id,
        mode="voice",
        total_questions=quiz_set.question_count,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    response = client.post(f"/api/v1/quiz/attempts/{attempt.id}/complete")
    assert response.status_code == 400


def test_get_attempt_returns_summary(client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    response = client.get(f"/api/v1/quiz/attempts/{attempt_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == attempt_id
    assert body["mode"] == "web"
    assert body["total_questions"] == quiz_set.question_count


def test_get_attempt_returns_404_for_missing(client, seeded_user):
    response = client.get("/api/v1/quiz/attempts/9999999")
    assert response.status_code == 404


def test_get_attempt_returns_403_for_other_user(client, session, seeded_user, other_user):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    client_b = TestClient(client.app, headers=_credentials_for(other_user))
    response = client_b.get(f"/api/v1/quiz/attempts/{attempt_id}")
    assert response.status_code == 403


def test_quiz_routes_inherit_per_user_rate_limit(client, session, seeded_user, monkeypatch):
    from sra.api.app import create_app
    from sra.db.session import get_session

    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_CAPACITY", "2")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND", "0.1")
    get_settings.cache_clear()

    clock = [1000.0]
    app = create_app(rate_limit_clock=lambda: clock[0])
    app.dependency_overrides[get_session] = lambda: session

    from datetime import UTC, datetime

    from sra.auth.google_oauth import issue_app_credential

    token = issue_app_credential(
        seeded_user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as auth_client:
        assert auth_client.get("/api/v1/quiz/today", headers=headers).status_code == 200
        assert auth_client.get("/api/v1/quiz/today", headers=headers).status_code == 200
        limited = auth_client.get("/api/v1/quiz/today", headers=headers)
        assert limited.status_code == 429
        assert limited.headers["Retry-After"]


# ---------------------------------------------------------------------------
# SRA-805 — Interview routes: start (mode=interview), turn (4 actions),
# GET plan + transcript, ownership / auth / rate-limit / status mappings.
# ---------------------------------------------------------------------------


phase8 = pytest.mark.phase8  # noqa: E402  -- marker alias below the Phase 4 marker


def _patch_interview_decision(monkeypatch, decisions):
    from sra.agents import interview_conversation_graph as _icg

    class _FakeChatModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is _icg.InterviewDecision
            return self

        def invoke(self, messages, **kwargs):
            return _icg.InterviewDecision(**decisions.pop(0))

    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel())


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


@phase8
def test_start_attempt_interview_returns_201_with_question(
    monkeypatch, client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    _patch_interview_planner(monkeypatch)

    response = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "interview"},
    )

    assert response.status_code == 201
    body = response.json()
    assert "attempt_id" in body
    assert body.get("question_text") is not None
    assert isinstance(body.get("transcript_so_far"), list)


@phase8
def test_start_attempt_interview_returns_201_creating_attempt_and_turn(
    monkeypatch, client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    _patch_interview_planner(monkeypatch)

    response = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts",
        json={"mode": "interview"},
    )
    assert response.status_code == 201
    attempt_id = response.json()["attempt_id"]
    from sra.db.models import InterviewTurn

    turns = session.exec(
        __import__("sqlmodel").select(InterviewTurn).where(InterviewTurn.attempt_id == attempt_id)
    ).all()
    assert len(turns) == 1
    assert turns[0].role == "ai"


@phase8
def test_interview_turn_answer_route_persists_turns_and_returns_next_question(
    monkeypatch, client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    _patch_interview_planner(monkeypatch)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "interview"}
    )
    attempt_id = start.json()["attempt_id"]

    next_decision = {
        "score": 80,
        "rationale": "ok",
        "feedback": "good",
        "next_action": "ask_new",
        "next_question_text": "next question",
    }
    _patch_interview_decision(monkeypatch, [next_decision])

    response = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "the answer"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 80
    assert body["question_text"] == "next question"
    assert body["finished"] is False


@phase8
def test_interview_turn_end_route_completes_attempt(
    monkeypatch, client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    _patch_interview_planner(monkeypatch)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "interview"}
    )
    attempt_id = start.json()["attempt_id"]

    response = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "end"},
    )

    assert response.status_code == 200
    assert response.json()["finished"] is True


@phase8
def test_interview_turn_wrong_mode_returns_400(
    monkeypatch, client, session, seeded_user
):
    """An MCQ attempt's interview-turn call is a 400."""
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    start = client.post(f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "web"})
    attempt_id = start.json()["attempt_id"]

    response = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "x"},
    )

    assert response.status_code == 400


@phase8
def test_interview_turn_completed_attempt_returns_409(
    monkeypatch, client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    _patch_interview_planner(monkeypatch)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "interview"}
    )
    attempt_id = start.json()["attempt_id"]
    client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "end"},
    )

    response = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "x"},
    )
    assert response.status_code == 409


@phase8
def test_interview_turn_other_user_returns_403(
    monkeypatch, client, session, seeded_user, other_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
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

    response = other_client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "x"},
    )
    assert response.status_code == 403


@phase8
def test_interview_turn_unauthenticated_returns_401(
    monkeypatch, client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    _patch_interview_planner(monkeypatch)
    start = client.post(
        f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "interview"}
    )
    attempt_id = start.json()["attempt_id"]

    anon = TestClient(client.app)
    response = anon.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "x"},
    )
    assert response.status_code == 401


@phase8
def test_get_interview_returns_plan_and_turns(
    monkeypatch, client, session, seeded_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
    _patch_interview_planner(monkeypatch)
    client.post(f"/api/v1/quiz/{quiz_set.id}/attempts", json={"mode": "interview"})

    response = client.get(
        f"/api/v1/quiz/attempts/{client.app.state.last_attempt_id_for_test}/interview"
    )
    # In the e2e tests we hit an explicit attempt id, this unit-level call uses
    # the most recently created attempt for this user via the read-only query.
    if response.status_code == 404:
        # Fall back to GET via look-up: find the most recent attempt for the user
        from sqlmodel import select

        from sra.db.models import QuizAttempt

        attempt = session.exec(
            select(QuizAttempt).where(QuizAttempt.user_id == seeded_user.id)
        ).all()[-1]
        response = client.get(f"/api/v1/quiz/attempts/{attempt.id}/interview")
    assert response.status_code == 200
    body = response.json()
    assert "plan" in body
    assert "turns" in body
    assert "attempt" in body
    assert body["plan"]["quiz_set_id"] == quiz_set.id


@phase8
def test_get_interview_returns_403_for_other_user(
    monkeypatch, client, session, seeded_user, other_user
):
    entry = _seed_entry(session, seeded_user.id)
    quiz_set = _seed_quiz_set(session, entry)
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


@phase8
def test_get_interview_returns_404_for_missing_attempt(client):
    response = client.get("/api/v1/quiz/attempts/9999999/interview")
    assert response.status_code == 404