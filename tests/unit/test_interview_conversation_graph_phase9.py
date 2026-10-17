# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-904 — Per-turn grader prompt uplift.

Every test in this module is expected to fail today because the
``sra.agents.interview_conversation_graph`` rewrite has not landed yet:

  * ``_grading_prompt`` does not exist; calling it raises ``AttributeError``.
  * ``_build_decision_prompt`` does not yet consume
    ``source_passage_quote`` / ``ideal_answer_rubric`` /
    ``bloom_level`` from the plan item; tests assert those strings appear
    in the rendered human message and find them missing.
  * ``process_interview_turn`` does not log the
    "interview plan item missing source_passage_quote; using fallback
    prompt" warning when the plan lacks the new keys.

The tests pin a *pure* per-turn grading seam
(``_grading_prompt(plan_item, transcript, answer, *, source_passage, ideal_answer_rubric, bloom_level) -> tuple[SystemMessage, HumanMessage]``)
mirroring the public seam the implementer is expected to add.
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


def _make_plan(
    session,
    quiz_set: QuizSet,
    *,
    with_new_keys: bool = True,
    n: int = 1,
) -> None:
    """Seed an InterviewPlan row.

    ``with_new_keys=True`` writes the three Phase 9 fields; otherwise it
    writes the legacy 3-key shape that older plans carry.
    """
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
                "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable as d_k grows"
            )
            item["ideal_answer_rubric"] = (
                f"A correct answer states that sqrt(d_k) keeps dot products in a low-variance range {i}."
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


class _FakeChatModel:
    """A scripted LLM that returns an InterviewDecision per call."""

    def __init__(self, decisions, *, raise_on_invoke: bool = False):
        self._decisions = list(decisions)
        self._raise = raise_on_invoke
        self.seen_messages: list = []

    def with_structured_output(self, schema, **kwargs):
        icg = _import_icg()
        assert schema is icg.InterviewDecision
        return self

    def invoke(self, messages, **kwargs):
        self.seen_messages.extend(messages)
        if self._raise:
            raise RuntimeError("simulated LLM failure")
        if not self._decisions:
            raise AssertionError("no scripted decision left")
        return self._decisions.pop(0)


def _grading_prompt(plan_item, transcript, answer, **kwargs):
    """Pure renderer call (new seam).

    Today this raises ``AttributeError`` because ``_grading_prompt`` does
    not yet exist on ``sra.agents.interview_conversation_graph``.
    """
    icg = _import_icg()
    return icg._grading_prompt(plan_item, transcript, answer, **kwargs)


# ---------------------------------------------------------------------------
# _grading_prompt — pure prompt-renderer contract
# ---------------------------------------------------------------------------


_PLAN_ITEM_NEW = {
    "subtopic": "scaled dot-product attention",
    "difficulty": "medium",
    "focus_prompt": "Walk me through why we divide by sqrt(d_k).",
}


def test_grading_prompt_includes_source_passage_quote_when_present():
    system, human = _grading_prompt(
        _PLAN_ITEM_NEW,
        [],
        "we divide by sqrt(d_k)",
        source_passage_quote="We scale by 1/sqrt(d_k) to keep the variance of QK^T stable",
        ideal_answer_rubric="sqrt(d_k) keeps dot products in a low-variance range.",
        bloom_level="apply",
    )
    assert "We scale by 1/sqrt(d_k)" in human.content


def test_grading_prompt_includes_ideal_answer_rubric_when_present():
    system, human = _grading_prompt(
        _PLAN_ITEM_NEW,
        [],
        "we divide by sqrt(d_k)",
        source_passage_quote="We scale by 1/sqrt(d_k) to keep the variance of QK^T stable",
        ideal_answer_rubric="sqrt(d_k) keeps dot products in a low-variance range.",
        bloom_level="apply",
    )
    assert "sqrt(d_k) keeps dot products in a low-variance range." in human.content


def test_grading_prompt_includes_bloom_level_when_present():
    system, human = _grading_prompt(
        _PLAN_ITEM_NEW,
        [],
        "we divide by sqrt(d_k)",
        source_passage_quote="We scale by 1/sqrt(d_k) to keep the variance of QK^T stable",
        ideal_answer_rubric="sqrt(d_k) keeps dot products in a low-variance range.",
        bloom_level="apply",
    )
    assert "apply" in human.content.lower()


def test_grading_prompt_falls_back_to_legacy_prompt_when_plan_lacks_new_keys(
    caplog,
):
    """When the plan item has no source_passage_quote / ideal_answer_rubric,
    the prompt degenerates to today's shape and a logger.warning fires.
    """
    # The pytest caplog handler is occasionally stripped by upstream
    # ``create_app()`` / ``init_db()`` calls (a pre-existing test-infra
    # issue that affects any caplog-based test). To stay order-independent
    # we install our own capture handler directly on the conversation
    # graph logger at WARNING level, then assert against it.
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
            captured.append(record)

    import sra.agents.interview_conversation_graph as _icg_mod  # noqa: WPS433
    _capture = _Capture(level=logging.WARNING)
    _icg_mod.logger.addHandler(_capture)
    try:
        icg = _import_icg()
        plan_item = {
            "subtopic": "scaled dot-product attention",
            "difficulty": "medium",
            "focus_prompt": "Walk me through why we divide by sqrt(d_k).",
        }
        transcript: list[dict] = [
            {"role": "ai", "content": "q1"},
            {"role": "user", "content": "a1"},
        ]
        answer = "we divide by sqrt(d_k)"
        system, human = icg._grading_prompt(plan_item, transcript, answer)
    finally:
        _icg_mod.logger.removeHandler(_capture)

    # Legacy behaviour: the rubric / source passage are absent from the
    # rendered prompt.
    assert "source_passage_quote" not in human.content
    assert "ideal_answer_rubric" not in human.content
    # And a warning was emitted exactly once for the missing fields.
    matching = [
        record
        for record in captured
        if record.levelno == logging.WARNING
        and "source_passage_quote" in record.getMessage()
    ]
    assert len(matching) >= 1


def test_grading_prompt_carries_transcript_so_far_unmodified():
    """The transcript_so_far the caller passes is rendered verbatim."""
    transcript: list[dict] = [
        {"role": "ai", "content": "first question"},
        {"role": "user", "content": "first answer"},
        {"role": "ai", "content": "follow-up question"},
    ]
    answer = "second answer"
    _, human = _grading_prompt(
        _PLAN_ITEM_NEW,
        transcript,
        answer,
        source_passage_quote="We scale by 1/sqrt(d_k) to keep the variance of QK^T stable",
        ideal_answer_rubric="sqrt(d_k) keeps dot products in a low-variance range.",
        bloom_level="apply",
    )
    assert "first question" in human.content
    assert "first answer" in human.content
    assert "follow-up question" in human.content


# ---------------------------------------------------------------------------
# process_interview_turn — flow tests
# ---------------------------------------------------------------------------


def test_process_interview_turn_uses_source_grounded_decision_when_plan_has_new_keys(
    monkeypatch, session, seeded_user
):
    """With the new keys present, the LLM is called with a prompt that
    contains the source passage and rubric, and ``decision.score`` is
    persisted on the user turn."""
    from sra.db.models import InterviewTurn

    icg = _import_icg()
    quiz_set = _make_quiz_set(session, seeded_user.id)
    _make_plan(session, quiz_set, with_new_keys=True)
    attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)

    decision = icg.InterviewDecision(
        score=80,
        rationale="Matches the source passage rubric.",
        feedback="good",
        next_action="ask_new",
        next_question_text="next q",
    )
    fake = _FakeChatModel([decision])
    monkeypatch.setattr(icg, "_get_chat_model", lambda: fake)

    # Start (no LLM call).
    icg.process_interview_turn(
        session=session, attempt=attempt, action="start", answer_text=None
    )
    # Answer (one LLM call).
    result = icg.process_interview_turn(
        session=session,
        attempt=attempt,
        action="answer",
        answer_text="we divide by sqrt(d_k)",
    )
    assert result.score == 80

    # The grading LLM was invoked once; check the messages it saw.
    assert len(fake.seen_messages) >= 2
    blob = " ".join(
        getattr(m, "content", "") if hasattr(m, "content") else str(m)
        for m in fake.seen_messages
    )
    assert "We scale by 1/sqrt(d_k)" in blob
    assert "sqrt(d_k) keeps dot products in a low-variance range" in blob

    # The user-turn score was persisted.
    user_turns = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "user",
        )
    ).all()
    assert any(t.score == 80 for t in user_turns)


def test_process_interview_turn_falls_back_to_legacy_when_plan_lacks_new_keys_with_warning(
    monkeypatch, session, seeded_user, caplog
):
    """When the plan item has no source_passage_quote / ideal_answer_rubric,
    behaviour is unchanged from Phase 8 and one warning is logged."""
    from sra.db.models import InterviewTurn

    # Per-test capture handler so the test is order-independent of pytest's
    # caplog handler (which can be stripped by upstream ``create_app()``
    # calls -- a pre-existing infrastructure issue).
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
            captured.append(record)

    import sra.agents.interview_conversation_graph as _icg_mod  # noqa: WPS433
    _capture = _Capture(level=logging.WARNING)
    _icg_mod.logger.addHandler(_capture)
    try:
        icg = _import_icg()
        quiz_set = _make_quiz_set(session, seeded_user.id)
        _make_plan(session, quiz_set, with_new_keys=False)
        attempt = _make_interview_attempt(session, seeded_user.id, quiz_set)

        decision = icg.InterviewDecision(
            score=60,
            rationale="ok",
            feedback="fine",
            next_action="ask_new",
            next_question_text="next q",
        )
        fake = _FakeChatModel([decision])
        monkeypatch.setattr(icg, "_get_chat_model", lambda: fake)

        icg.process_interview_turn(
            session=session, attempt=attempt, action="start", answer_text=None
        )
        result = icg.process_interview_turn(
            session=session,
            attempt=attempt,
            action="answer",
            answer_text="legacy answer",
        )
    finally:
        _icg_mod.logger.removeHandler(_capture)

    assert result.score == 60
    # The legacy fallback logged a warning about the missing fields.
    warnings = [
        record
        for record in captured
        if record.levelno == logging.WARNING
        and (
            "source_passage_quote" in record.getMessage()
            or "missing" in record.getMessage().lower()
        )
    ]
    assert len(warnings) >= 1

    # No crash; the user-turn row was persisted with a score.
    user_turns = session.exec(
        select(InterviewTurn).where(
            InterviewTurn.attempt_id == attempt.id,
            InterviewTurn.role == "user",
        )
    ).all()
    assert any(t.score == 60 for t in user_turns)