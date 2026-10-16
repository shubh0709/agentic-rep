"""SRA-803 — Per-turn ``process_interview_turn`` contracts.

State is reloaded from the database on each call; the LLM seam
(``_get_chat_model``) is patched so the tests never hit OpenAI.

``InterviewTurn`` and ``InterviewPlan`` are SRA-801 symbols that are not yet
declared on ``sra.db.models``. Tests import them lazily so a missing symbol
fails that test, not the entire module.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false

from datetime import date
from typing import Any

import pytest
from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.db.models import (
    QuizAttempt,
    QuizSet,
    StudyEntry,
    StudyEntryCreate,
)
from sra.db.repository import create_quiz_set

pytestmark = pytest.mark.phase8


@pytest.fixture
def interview_conversation_graph():
    from sra.agents import interview_conversation_graph as _icg

    return _icg


def _import_interview_models():
    """Resolve the SRA-801 model classes lazily so a missing symbol fails the test."""
    from sra.db.models import InterviewPlan, InterviewPlanCreate, InterviewTurn

    return InterviewPlan, InterviewPlanCreate, InterviewTurn


class _FakeChatModel:
    """A scripted LLM that returns an InterviewDecision per call."""

    def __init__(self, decisions: list, *, raise_on_invoke: bool = False):
        self._decisions = list(decisions)
        self._raise = raise_on_invoke
        self.seen_messages: list[Any] = []

    def with_structured_output(self, schema, **kwargs):
        from sra.agents import interview_conversation_graph as _icg

        assert schema is _icg.InterviewDecision
        return self

    def invoke(self, messages, **kwargs):

        self.seen_messages.extend(messages)
        if self._raise:
            raise RuntimeError("simulated LLM failure")
        if not self._decisions:
            raise AssertionError("no scripted decision left")
        return self._decisions.pop(0)


def _make_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int) -> QuizSet:
    entry = _make_entry(session, user_id)
    drafts = [
        MCQDraft(question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""),
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


def _make_interview_attempt(session, user_id: int, quiz_set: QuizSet) -> QuizAttempt:
    attempt = QuizAttempt(
        user_id=user_id,
        quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
        mode="interview",
        total_questions=quiz_set.question_count,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def _make_plan(session, quiz_set: QuizSet, n: int = 1) -> None:
    InterviewPlan, InterviewPlanCreate, _ = _import_interview_models()
    plan = InterviewPlan.model_validate(
        InterviewPlanCreate(
            quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
            outline=[
                {"subtopic": f"sub{i}", "difficulty": "medium", "focus_prompt": f"focus-{i}"}
                for i in range(n)
            ],
            target_questions=n,
        )
    )
    session.add(plan)
    session.commit()


def test_module_exposes_required_public_symbols(interview_conversation_graph):
    for name in (
        "process_interview_turn",
        "InterviewDecision",
        "InterviewLLMError",
        "InterviewAttemptFullError",
        "InterviewAttemptAlreadyCompletedError",
        "_get_chat_model",
    ):
        assert hasattr(interview_conversation_graph, name), name


def test_start_action_persists_first_ai_turn_and_returns_first_question(
    monkeypatch, session, seeded_user, interview_conversation_graph
):
    _, _, InterviewTurn = _import_interview_models()
    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(
        interview_conversation_graph,
        "_get_chat_model",
        lambda: _FakeChatModel([]),  # start does not call LLM
    )

    result = interview_conversation_graph.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )

    assert result.question_text is not None
    assert result.finished is False
    ai_turns = session.exec(
        select(InterviewTurn).where(InterviewTurn.attempt_id == attempt.id)
    ).all()
    assert len(ai_turns) == 1
    assert ai_turns[0].role == "ai"


def test_answer_action_persists_user_then_ai_turns(
    monkeypatch, session, seeded_user, interview_conversation_graph
):
    from sra.agents import interview_conversation_graph as _icg

    _, _, InterviewTurn = _import_interview_models()
    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)

    decision = _icg.InterviewDecision(
        score=80,
        rationale="ok",
        feedback="good",
        next_action="ask_new",
        next_question_text="next q",
    )
    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel([decision]))

    _icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    result = _icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="the user said this",
    )

    assert result.score == 80
    assert result.question_text == "next q"
    assert result.finished is False

    turns = session.exec(
        select(InterviewTurn).where(InterviewTurn.attempt_id == attempt.id).order_by(InterviewTurn.id)
    ).all()
    roles = [t.role for t in turns]
    # start persists one ai turn; answer persists user, ai (feedback), ai (next q)
    assert "user" in roles
    assert roles.count("ai") >= 2


def test_answer_action_finish_persists_system_summary_and_completes(
    monkeypatch, session, seeded_user, interview_conversation_graph
):
    from sra.agents import interview_conversation_graph as _icg

    _, _, InterviewTurn = _import_interview_models()
    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)

    finish_decision = _icg.InterviewDecision(
        score=70,
        rationale="ok",
        feedback="good",
        next_action="finish",
        next_question_text=None,
        summary="you answered one question",
    )
    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel([finish_decision]))

    _icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    result = _icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="the answer",
    )

    assert result.finished is True
    assert result.summary is not None

    summary_turn = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "system_summary",
        )
    ).one()
    assert summary_turn.id is not None


def test_next_action_advances_cursor_on_plan(
    monkeypatch, session, seeded_user, interview_conversation_graph
):
    from sra.agents import interview_conversation_graph as _icg

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set, n=2)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel([]))

    _icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    attempt_before = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt.id)).one()
    cursor_before = attempt_before.cursor_on_plan

    _icg.process_interview_turn(
        session=session, attempt=attempt, action="next", answer_text=None
    )

    attempt_after = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt.id)).one()
    assert attempt_after.cursor_on_plan > cursor_before


def test_end_action_persists_system_summary(
    monkeypatch, session, seeded_user, interview_conversation_graph
):
    from sra.agents import interview_conversation_graph as _icg

    _, _, InterviewTurn = _import_interview_models()
    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel([]))

    _icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    result = _icg.process_interview_turn(
        session=session, attempt=attempt, action="end", answer_text=None
    )

    assert result.finished is True
    assert result.summary is not None
    summary_turn = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "system_summary",
        )
    ).one()
    assert summary_turn.id is not None


def test_completed_attempt_raises_already_completed_error(
    monkeypatch, session, seeded_user, interview_conversation_graph
):
    from sra.agents import interview_conversation_graph as _icg

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)
    attempt.completed_at = attempt.started_at
    session.add(attempt)
    session.commit()
    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel([]))

    with pytest.raises(_icg.InterviewAttemptAlreadyCompletedError):
        _icg.process_interview_turn(
            session=session, attempt=attempt, action="answer", answer_text="x"
        )


def test_llm_failure_raises_typed_error(
    monkeypatch, session, seeded_user, interview_conversation_graph
):
    from sra.agents import interview_conversation_graph as _icg

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel([], raise_on_invoke=True))

    _icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    with pytest.raises(_icg.InterviewLLMError):
        _icg.process_interview_turn(
            session=session,
            attempt=attempt,
            action="answer",
            answer_text="x",
        )


def test_interview_decision_validates_score_in_range(
    interview_conversation_graph,
):
    """Score outside [0,100] is a typed ``InterviewLLMError``."""
    from pydantic import ValidationError

    from sra.agents import interview_conversation_graph as _icg

    with pytest.raises(ValidationError):
        _icg.InterviewDecision(
            score=-1,
            rationale="x",
            feedback="y",
            next_action="ask_new",
            next_question_text="q",
        )
    with pytest.raises(ValidationError):
        _icg.InterviewDecision(
            score=101,
            rationale="x",
            feedback="y",
            next_action="ask_new",
            next_question_text="q",
        )


def test_interview_decision_requires_next_question_for_non_finish(
    interview_conversation_graph,
):
    from pydantic import ValidationError

    from sra.agents import interview_conversation_graph as _icg

    with pytest.raises(ValidationError):
        _icg.InterviewDecision(
            score=50,
            rationale="x",
            feedback="y",
            next_action="ask_new",
            next_question_text=None,
        )


def test_interview_decision_requires_summary_for_finish(
    interview_conversation_graph,
):
    from pydantic import ValidationError

    from sra.agents import interview_conversation_graph as _icg

    with pytest.raises(ValidationError):
        _icg.InterviewDecision(
            score=50,
            rationale="x",
            feedback="y",
            next_action="finish",
            next_question_text=None,
            summary=None,
        )
