
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-402 MCQDraft / MCQDrafts schema contracts."""

import pytest
from pydantic import ValidationError

from sra.agents.schemas import MCQDraft, MCQDrafts

pytestmark = pytest.mark.phase4


def test_mcq_draft_round_trips_with_expected_fields():
    draft = MCQDraft(
        question_text="Which wavelength drives photosystem II?",
        options=["680 nm", "700 nm", "500 nm"],
        correct_option_index=0,
        explanation="Photosystem II absorbs light at 680 nm.",
    )

    assert draft.question_text == "Which wavelength drives photosystem II?"
    assert draft.options == ["680 nm", "700 nm", "500 nm"]
    assert draft.correct_option_index == 0
    assert draft.explanation == "Photosystem II absorbs light at 680 nm."


def test_mcq_draft_options_default_to_empty_list():
    draft = MCQDraft(question_text="x", correct_option_index=0, explanation="")

    assert draft.options == []


def test_mcq_draft_explanation_defaults_to_empty_string():
    draft = MCQDraft(question_text="x", options=["a"], correct_option_index=0)

    assert draft.explanation == ""


def test_mcq_draft_correct_option_index_must_be_non_negative_integer():
    with pytest.raises(ValidationError):
        MCQDraft(question_text="x", options=["a"], correct_option_index=-1)


def test_mcq_draft_correct_option_index_must_be_integer():
    with pytest.raises(ValidationError):
        MCQDraft(question_text="x", options=["a"], correct_option_index="0")  # pyright: ignore[reportArgumentType]


def test_mcq_drafts_carries_a_list_of_drafts():
    drafts = MCQDrafts(
        drafts=[
            MCQDraft(question_text="q1", options=["a", "b"], correct_option_index=0),
            MCQDraft(question_text="q2", options=["a", "b"], correct_option_index=1),
        ]
    )

    assert len(drafts.drafts) == 2
    assert all(isinstance(d, MCQDraft) for d in drafts.drafts)
    assert [d.question_text for d in drafts.drafts] == ["q1", "q2"]


def test_mcq_drafts_defaults_to_empty_list():
    container = MCQDrafts()

    assert container.drafts == []


def test_mcq_drafts_serializes_via_model_dump():
    container = MCQDrafts(
        drafts=[MCQDraft(question_text="q", options=["a"], correct_option_index=0, explanation="e")]
    )

    payload = container.model_dump()

    assert payload == {
        "drafts": [
            {
                "question_text": "q",
                "options": ["a"],
                "correct_option_index": 0,
                "explanation": "e",
            }
        ]
    }


# ---------------------------------------------------------------------------
# SRA-806 — Interview DTOs round-trip and ``extra="forbid"`` tests.
# ---------------------------------------------------------------------------


phase8 = pytest.mark.phase8  # noqa: E402  -- marker alias below the Phase 4 marker


@phase8
def test_interview_plan_out_round_trips():
    from sra.api.schemas import InterviewPlanOut

    plan = InterviewPlanOut(
        id=1,
        quiz_set_id=2,
        outline=[{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}],
        target_questions=1,
        created_at="2026-10-14T12:00:00Z",
    )
    payload = plan.model_dump()
    reloaded = InterviewPlanOut.model_validate(payload)
    assert reloaded == plan


@phase8
def test_interview_turn_out_round_trips():
    from sra.api.schemas import InterviewTurnOut

    turn = InterviewTurnOut(
        id=7,
        role="ai",
        content="hello",
        score=None,
        outline_index=0,
        created_at="2026-10-14T12:00:00Z",
    )
    payload = turn.model_dump()
    reloaded = InterviewTurnOut.model_validate(payload)
    assert reloaded == turn


@phase8
def test_interview_summary_out_round_trips():
    from sra.api.schemas import InterviewSummaryOut

    summary = InterviewSummaryOut(
        score=80,
        answered_count=3,
        remaining_count=2,
        summary_text="summary text",
    )
    payload = summary.model_dump()
    reloaded = InterviewSummaryOut.model_validate(payload)
    assert reloaded == summary


@phase8
def test_interview_turn_request_round_trips():
    from sra.api.schemas import InterviewTurnRequest

    body = InterviewTurnRequest(action="answer", answer_text="hi")
    assert body.action == "answer"
    assert body.answer_text == "hi"
    dumped = body.model_dump()
    reloaded = InterviewTurnRequest.model_validate(dumped)
    assert reloaded.action == "answer"


@phase8
def test_interview_turn_request_rejects_unknown_action():
    from sra.api.schemas import InterviewTurnRequest

    with pytest.raises(ValidationError):
        InterviewTurnRequest(action="bogus", answer_text="x")


@phase8
def test_interview_turn_request_extra_forbid_rejects_unknown_key():
    from sra.api.schemas import InterviewTurnRequest

    with pytest.raises(ValidationError):
        InterviewTurnRequest.model_validate(
            {"action": "answer", "answer_text": "x", "unexpected": "field"}
        )


@phase8
def test_interview_turn_request_answer_action_requires_non_empty_text():
    from sra.api.schemas import InterviewTurnRequest

    with pytest.raises(ValidationError):
        InterviewTurnRequest(action="answer", answer_text="   ")


@phase8
def test_interview_detail_out_round_trips_with_optional_summary():
    from sra.api.schemas import (
        InterviewDetailOut,
        InterviewPlanOut,
        InterviewSummaryOut,
        InterviewTurnOut,
        QuizAttemptOut,
    )

    detail = InterviewDetailOut(
        plan=InterviewPlanOut(
            id=1,
            quiz_set_id=2,
            outline=[{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}],
            target_questions=1,
            created_at="2026-10-14T12:00:00Z",
        ),
        turns=[InterviewTurnOut(id=1, role="ai", content="q", created_at="2026-10-14T12:00:00Z")],
        summary=InterviewSummaryOut(score=80, answered_count=1, remaining_count=0, summary_text="ok"),
        attempt=QuizAttemptOut(
            id=1,
            quiz_set_id=2,
            mode="interview",
            started_at="2026-10-14T12:00:00Z",
            completed_at=None,
            score=None,
            total_questions=1,
            cursor_on_plan=0,
        ),
    )
    dumped = detail.model_dump()
    reloaded = InterviewDetailOut.model_validate(dumped)
    assert reloaded.summary is not None
    assert reloaded.summary.answered_count == 1


@phase8
def test_quiz_attempt_out_always_includes_cursor_on_plan_with_default_zero():
    from datetime import UTC, datetime

    from sra.api.schemas import QuizAttemptOut

    attempt = QuizAttemptOut(
        id=1,
        quiz_set_id=2,
        mode="web",
        started_at=datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC),
        completed_at=None,
        score=None,
        total_questions=3,
    )
    assert attempt.cursor_on_plan == 0


@phase8
def test_preferences_in_rejects_unknown_fields():
    from sra.api.schemas import PreferencesIn

    with pytest.raises(ValidationError):
        PreferencesIn.model_validate(
            {
                "preferred_review_time": "09:00",
                "timezone": "UTC",
                "quiz_question_count": 10,
                "quiz_option_count": 4,
                "interview_question_count": 6,
                "interview_target_minutes": 5,
                "unknown_field": "x",
            }
        )


@phase8
def test_start_attempt_request_accepts_interview_mode():
    from sra.api.schemas import StartAttemptRequest

    body = StartAttemptRequest.model_validate({"mode": "interview"})
    assert body.mode == "interview"


@phase8
def test_start_attempt_request_rejects_unknown_mode():
    from sra.api.schemas import StartAttemptRequest

    with pytest.raises(ValidationError):
        StartAttemptRequest.model_validate({"mode": "telepathy"})