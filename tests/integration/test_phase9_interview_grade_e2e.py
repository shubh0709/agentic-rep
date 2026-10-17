# pyright: reportAttributeAccessIssue=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false
"""SRA-904 — End-to-end interview lifecycle through the conversation graph.

Two cases:

1. Plan items carry the new Phase 9 fields (source_passage_quote,
   ideal_answer_rubric, bloom_level). The grading LLM receives those
   anchors in the prompt, and the user-turn ``rationale`` it returns
   reflects the planner-supplied rubric.

2. Plan items lack the new keys (legacy outline from an older planner).
   Behaviour degrades gracefully — the same path runs, one warning is
   logged, no crash.

Today both tests fail: the first because the planner / graph never
thread the new fields through; the second because the "fallback
warning" log message does not exist.
"""

from __future__ import annotations

import logging
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

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_icg() -> Any:
    from sra.agents import interview_conversation_graph as _icg

    return _icg


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(
            topic="Transformer attention",
            summary="how attention is computed and why multi-head works",
            raw_transcript="today I read about transformers",
        ),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int) -> QuizSet:
    entry = _seed_entry(session, user_id)
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


def _make_plan(
    session,
    quiz_set: QuizSet,
    *,
    with_new_keys: bool = True,
    n: int = 2,
) -> None:
    from sra.db.models import InterviewPlan, InterviewPlanCreate

    items: list[dict] = []
    for i in range(n):
        item: dict = {
            "subtopic": f"sub{i}",
            "difficulty": "medium",
            "focus_prompt": f"focus-{i}",
        }
        if with_new_keys:
            item["source_passage_quote"] = (
                "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable"
            )
            item["ideal_answer_rubric"] = (
                f"sqrt(d_k) keeps dot products in a low-variance range {i}."
            )
            item["bloom_level"] = "apply"
        items.append(item)
    plan = InterviewPlan.model_validate(
        InterviewPlanCreate(
            quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
            outline=items,
            target_questions=n,
        )
    )
    session.add(plan)
    session.commit()


def _make_attempt(session, user_id: int, quiz_set: QuizSet) -> QuizAttempt:
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


class _FakeChatModel:
    """Fake LLM that returns an InterviewDecision per call."""

    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.seen_messages: list = []

    def with_structured_output(self, schema, **kwargs):
        icg = _import_icg()
        assert schema is icg.InterviewDecision
        return self

    def invoke(self, messages, **kwargs):
        icg = _import_icg()
        self.seen_messages.extend(messages)
        return icg.InterviewDecision(**self._decisions.pop(0))


# ---------------------------------------------------------------------------
# Case 1 — plan items carry the new keys; transcript reflects the rubric.
# ---------------------------------------------------------------------------


def test_full_lifecycle_rationale_cites_source_passage_and_rubric(
    monkeypatch, session, seeded_user
):
    """start → answer → next → answer → end; the LLM saw the rubric and
    the transcript carries the user turns + system summary as before."""
    icg = _import_icg()
    from sra.db.models import InterviewTurn

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set, with_new_keys=True, n=2)
    attempt = _make_attempt(session, seeded_user.id, quiz_set)

    decisions = [
        dict(
            score=80,
            rationale=(
                "Answer matches the source passage: we scale by 1/sqrt(d_k) to "
                "keep the variance of QK^T stable; rubric criteria satisfied."
            ),
            feedback="good",
            next_action="ask_new",
            next_question_text="next q",
        ),
        dict(
            score=85,
            rationale=(
                "Concatenation lets each head learn a distinct subspace, per "
                "the supplied ideal_answer_rubric."
            ),
            feedback="good2",
            next_action="finish",
            next_question_text=None,
            summary="you answered two questions",
        ),
    ]
    fake = _FakeChatModel(decisions)
    monkeypatch.setattr(icg, "_get_chat_model", lambda: fake)

    icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    first = icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="we divide by sqrt(d_k) to keep the variance of QK^T stable",
    )
    assert first.finished is False

    advance = icg.process_interview_turn(
        session=session, attempt=attempt, action="next", answer_text=None
    )
    assert advance.finished is False

    second = icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="concatenation lets each head learn a distinct subspace",
    )
    assert second.finished is True

    # The grading LLM was called twice. Both invocations saw the source
    # passage / rubric in their prompt because the plan items carry them.
    assert len(fake.seen_messages) >= 4
    blob = " ".join(
        getattr(m, "content", "") if hasattr(m, "content") else str(m)
        for m in fake.seen_messages
    )
    assert "We scale by 1/sqrt(d_k)" in blob
    assert "sqrt(d_k) keeps dot products in a low-variance range" in blob

    # Transcript carries the LLM rationale that cites the source passage.
    user_turns = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "user",
        ).order_by(InterviewTurn.id)
    ).all()
    assert len(user_turns) == 2
    assert user_turns[0].score == 80
    assert user_turns[1].score == 85

    # The system_summary turn persisted at end.
    summary_turns = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "system_summary",
        )
    ).all()
    assert len(summary_turns) == 1


# ---------------------------------------------------------------------------
# Case 2 — plan items lack the new keys; graceful fallback.
# ---------------------------------------------------------------------------


def test_lifecycle_falls_back_gracefully_when_plan_lacks_new_keys(
    monkeypatch, session, seeded_user, caplog
):
    """Legacy plan shape: same flow runs, one warning is logged, no crash."""
    icg = _import_icg()
    from sra.db.models import InterviewTurn

    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set, with_new_keys=False, n=2)
    attempt = _make_attempt(session, seeded_user.id, quiz_set)

    decisions = [
        dict(
            score=60,
            rationale="ok",
            feedback="fine",
            next_action="ask_new",
            next_question_text="next q",
        ),
        dict(
            score=70,
            rationale="ok2",
            feedback="fine2",
            next_action="finish",
            next_question_text=None,
            summary="you answered two questions",
        ),
    ]
    fake = _FakeChatModel(decisions)
    monkeypatch.setattr(icg, "_get_chat_model", lambda: fake)

    icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    with caplog.at_level(
        logging.WARNING, logger="sra.agents.interview_conversation_graph"
    ):
        first = icg.process_interview_turn(
            session=session,
            attempt=attempt,
            action="answer",
            answer_text="legacy answer 1",
        )
    advance = icg.process_interview_turn(
        session=session, attempt=attempt, action="next", answer_text=None
    )
    second = icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="legacy answer 2",
    )

    assert first.score == 60
    assert advance.finished is False
    assert second.finished is True

    # One warning was emitted (at least once) for the missing keys.
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and (
            "source_passage_quote" in record.getMessage()
            or "missing" in record.getMessage().lower()
        )
    ]
    assert len(warnings) >= 1

    # No crash; both user-turn scores were persisted.
    user_turns = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "user",
        ).order_by(InterviewTurn.id)
    ).all()
    assert [t.score for t in user_turns] == [60, 70]