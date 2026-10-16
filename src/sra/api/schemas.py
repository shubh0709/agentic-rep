"""API request/response DTOs. Concrete, not a learning stub."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from sra.db.models import (
    MAX_INTERVIEW_QUESTION_COUNT,
    MAX_INTERVIEW_TARGET_MINUTES,
    MAX_OPTION_COUNT,
    MAX_QUESTION_COUNT,
    MIN_INTERVIEW_QUESTION_COUNT,
    MIN_INTERVIEW_TARGET_MINUTES,
)

_REVIEW_TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class IngestRequest(BaseModel):
    transcript: str


class EntryOut(BaseModel):
    id: int
    topic: str
    subtopic: str | None
    tags: list[str]
    summary: str
    raw_transcript: str
    created_at: datetime


class ReviewOut(BaseModel):
    id: int
    entry: EntryOut
    interval_label: str
    scheduled_date: date
    status: str


class ReviewDayOut(BaseModel):
    """One calendar day plus the reviews scheduled for it.

    Returned by ``GET /api/v1/reviews/upcoming`` so the dashboard can render a
    day-grouped timeline without having to re-bucket the flat list itself.
    The list of reviews is sorted by ``scheduled_date`` then ``id`` (a stable
    proxy for insertion order) so identical dates render deterministically.
    """

    date: date
    reviews: list[ReviewOut]


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str


class QuizQuestionOut(BaseModel):
    id: int
    question_text: str
    options: list[str]
    order_index: int


class QuizSetOut(BaseModel):
    id: int
    entry: EntryOut
    generated_on: date
    questions: list[QuizQuestionOut]


class StartAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["web", "voice", "interview"]


class SubmitAnswerRequest(BaseModel):
    question_id: int
    selected_option_index: int


class AnswerResult(BaseModel):
    is_correct: bool
    correct_option_index: int
    explanation: str | None


class VoiceTurnRequest(BaseModel):
    answer_text: str

    @field_validator("answer_text")
    @classmethod
    def _strip_and_require(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("answer_text must not be empty")
        return stripped


class VoiceTurnResponse(BaseModel):
    is_correct: bool | None
    feedback_text: str
    next_question_text: str | None
    finished: bool
    score: int | None


class QuizAttemptOut(BaseModel):
    id: int
    quiz_set_id: int
    mode: str
    started_at: datetime
    completed_at: datetime | None
    score: int | None
    total_questions: int
    cursor_on_plan: int = 0


class PreferencesIn(BaseModel):
    """PUT /api/v1/me/preferences request body.

    The four caller-supplied fields are required; ``wakeup_notification_sent_at``
    is an optional server-owned echo so the schema can round-trip into
    ``PreferencesOut`` for tests. The repository never reads it.
    """

    model_config = ConfigDict(extra="forbid")

    preferred_review_time: str
    timezone: str
    quiz_question_count: int
    quiz_option_count: int
    interview_question_count: int | None = None
    interview_target_minutes: int | None = None
    wakeup_notification_sent_at: datetime | None = None

    @field_validator("preferred_review_time")
    @classmethod
    def _check_review_time(cls, value: str) -> str:
        import re

        if re.fullmatch(_REVIEW_TIME_PATTERN, value) is None:
            raise ValueError(
                "preferred_review_time must be HH:MM with hour 00-23 and minute 00-59"
            )
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value

    @field_validator("quiz_question_count")
    @classmethod
    def _check_question_count(cls, value: int) -> int:
        if value < 1 or value > MAX_QUESTION_COUNT:
            raise ValueError(
                f"quiz_question_count must be between 1 and {MAX_QUESTION_COUNT}"
            )
        return value

    @field_validator("quiz_option_count")
    @classmethod
    def _check_option_count(cls, value: int) -> int:
        if value < 2 or value > MAX_OPTION_COUNT:
            raise ValueError(
                f"quiz_option_count must be between 2 and {MAX_OPTION_COUNT}"
            )
        return value

    @field_validator("interview_question_count")
    @classmethod
    def _check_interview_question_count(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value < MIN_INTERVIEW_QUESTION_COUNT or value > MAX_INTERVIEW_QUESTION_COUNT:
            raise ValueError(
                f"interview_question_count must be between "
                f"{MIN_INTERVIEW_QUESTION_COUNT} and {MAX_INTERVIEW_QUESTION_COUNT}"
            )
        return value

    @field_validator("interview_target_minutes")
    @classmethod
    def _check_interview_target_minutes(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value < MIN_INTERVIEW_TARGET_MINUTES or value > MAX_INTERVIEW_TARGET_MINUTES:
            raise ValueError(
                f"interview_target_minutes must be between "
                f"{MIN_INTERVIEW_TARGET_MINUTES} and {MAX_INTERVIEW_TARGET_MINUTES}"
            )
        return value


class PreferencesOut(BaseModel):
    """GET /api/v1/me/preferences response body.

    Carries all five persisted fields including the server-owned
    `wakeup_notification_sent_at` so the dashboard can render the
    last-sent timestamp without a second endpoint.
    """

    preferred_review_time: str
    timezone: str
    quiz_question_count: int
    quiz_option_count: int
    interview_question_count: int
    interview_target_minutes: int
    wakeup_notification_sent_at: datetime | None


def _strip_and_require_str(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty")
    return stripped


class InterviewOutlineItemOut(BaseModel):
    subtopic: str
    difficulty: Literal["easy", "medium", "hard"]
    focus_prompt: str

    @field_validator("subtopic", "focus_prompt")
    @classmethod
    def _strip_and_require(cls, value: str) -> str:
        return _strip_and_require_str(value)


class InterviewPlanOut(BaseModel):
    id: int
    quiz_set_id: int
    outline: list[InterviewOutlineItemOut]
    target_questions: int
    created_at: datetime


class InterviewTurnOut(BaseModel):
    id: int
    role: Literal["ai", "user", "system_summary"]
    content: str
    score: int | None = None
    outline_index: int | None = None
    created_at: datetime


class InterviewSummaryOut(BaseModel):
    score: int
    answered_count: int
    remaining_count: int
    summary_text: str


class InterviewTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["start", "answer", "next", "end"]
    answer_text: str | None = None

    @field_validator("answer_text")
    @classmethod
    def _strip_answer_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("answer_text must not be empty")
        return stripped

    @model_validator(mode="after")
    def _require_answer_text_for_answer(self) -> "InterviewTurnRequest":
        if self.action == "answer":
            if not self.answer_text or not self.answer_text.strip():
                raise ValueError("answer_text is required when action is 'answer'")
        return self


class InterviewTurnResponse(BaseModel):
    question_text: str | None
    score: int | None
    rationale: str | None
    feedback: str | None
    finished: bool
    summary: str | None
    transcript_so_far: list[InterviewTurnOut]
    attempt: QuizAttemptOut


class InterviewDetailOut(BaseModel):
    plan: InterviewPlanOut
    turns: list[InterviewTurnOut]
    summary: InterviewSummaryOut | None
    attempt: QuizAttemptOut