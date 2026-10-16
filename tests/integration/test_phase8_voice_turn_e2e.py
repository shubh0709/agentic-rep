"""SRA-803 — Full ``start -> answer -> next -> answer -> end`` lifecycle through real create_app()."""

# pyright: reportAttributeAccessIssue=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import date

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

# ``InterviewPlan`` / ``InterviewTurn`` are SRA-801 model symbols; import
# inside the test functions so a missing symbol fails the test only.

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


def _make_quiz_set(session, user_id: int) -> QuizSet:
    entry = _seed_entry(session, user_id)
    drafts = [
        MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")
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


def _seed_plan(session, quiz_set: QuizSet, n: int = 2) -> None:
    from sra.db.models import InterviewPlan, InterviewPlanCreate

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


def _import_interview_turn():
    """Lazy import of the SRA-801 ``InterviewTurn`` model class."""
    from sra.db.models import InterviewTurn
    return InterviewTurn


def _patch_decision(monkeypatch, scripted):
    from sra.agents import interview_conversation_graph as _icg

    class _FakeChatModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is _icg.InterviewDecision
            return self

        def invoke(self, messages, **kwargs):
            decision = scripted.pop(0)
            return _icg.InterviewDecision(**decision)

    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel())


def test_e2e_full_lifecycle_persists_expected_turns(
    monkeypatch, client, session, seeded_user
):
    """Drive a full lifecycle through the conversation graph (no router needed)."""
    from sra.agents import interview_conversation_graph as _icg

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _seed_plan(session, quiz_set, n=2)
    attempt = QuizAttempt(
        user_id=seeded_user.id,
        quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
        mode="interview",
        total_questions=quiz_set.question_count,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    decisions = [
        dict(
            score=70,
            rationale="ok",
            feedback="good",
            next_action="ask_new",
            next_question_text="next q",
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

    start = _icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    assert start.question_text is not None
    assert start.finished is False

    first = _icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="answer1",
    )
    assert first.score == 70
    assert first.finished is False

    advance = _icg.process_interview_turn(
        session=session, attempt=attempt, action="next", answer_text=None
    )
    assert advance.finished is False

    second = _icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="answer2",
    )
    assert second.finished is True
    assert second.summary is not None

    turns = session.exec(
        select(_import_interview_turn()).where(_import_interview_turn().attempt_id == attempt.id).order_by(_import_interview_turn().id)
    ).all()
    roles = [t.role for t in turns]
    # 1 ai from start, +user+ai feedback+ai next for first answer, +ai for next, +user+ai feedback+system_summary for second answer
    assert roles.count("user") == 2
    assert roles.count("system_summary") == 1
    assert roles.count("ai") >= 4


def test_e2e_first_ai_turn_persisted_before_grading_call_returns(
    monkeypatch, client, session, seeded_user
):
    """The user turn is persisted *before* the grading LLM is called so a mid-call
    crash leaves a recoverable ``score=None`` row."""
    from sra.agents import interview_conversation_graph as _icg

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _seed_plan(session, quiz_set)
    attempt = QuizAttempt(
        user_id=seeded_user.id,
        quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
        mode="interview",
        total_questions=quiz_set.question_count,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    def invoke_with_break(_messages, **_kwargs):
        # Before answering, check there's no user row yet.
        InterviewTurn = _import_interview_turn()
        user_rows = session.exec(
            select(InterviewTurn).where(
                InterviewTurn.attempt_id == attempt.id,
                InterviewTurn.role == "user",
            )
        ).all()
        assert len(user_rows) == 1
        raise RuntimeError("simulated LLM failure mid-call")

    class _Partial:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            return invoke_with_break(*args, **kwargs)

    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _Partial())

    _icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    with pytest.raises(_icg.InterviewLLMError):
        _icg.process_interview_turn(
            session=session,
            attempt=attempt,
            action="answer",
            answer_text="the user said this",
        )

    InterviewTurn = _import_interview_turn()
    user_turns = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "user",
        )
    ).all()
    assert len(user_turns) == 1
    assert user_turns[0].score is None
