"""API request/response DTOs. Concrete, not a learning stub."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator

from sra.db.models import MAX_OPTION_COUNT, MAX_QUESTION_COUNT

_REVIEW_TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class IngestRequest(BaseModel):
    transcript: str


class EntryOut(BaseModel):
    id: int
    topic: str
    subtopic: str | None
    tags: list[str]
    summary: str
    created_at: datetime


class ReviewOut(BaseModel):
    id: int
    entry: EntryOut
    interval_label: str
    scheduled_date: date
    status: str


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
    mode: Literal["web", "voice"]


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
    wakeup_notification_sent_at: datetime | None