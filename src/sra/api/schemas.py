"""API request/response DTOs. Concrete, not a learning stub."""

import base64
import binascii
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

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


class EntryUpdateRequest(BaseModel):
    """Fields the Library editor may change on an existing study note."""

    model_config = ConfigDict(extra="forbid")

    topic: str | None = None
    created_at: datetime | None = None

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("topic must not be empty")
        return stripped

    @model_validator(mode="after")
    def _require_change(self) -> "EntryUpdateRequest":
        if self.topic is None and self.created_at is None:
            raise ValueError("at least one editable field is required")
        return self


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
    # SRA-903: additive quality fields surfaced only on Phase 9 rows. They
    # default to ``None`` so the router's ``response_model_exclude_none``
    # flag (set on the relevant endpoints) drops them from the JSON
    # payload entirely when the underlying row has them NULL.
    bloom_level: str | None = None
    source_passage_quote: str | None = None
    distractor_misconception: str | None = None


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
    audio_enabled: StrictBool = True
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
    audio_enabled: bool
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
    rationale: str | None = None
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


class ReferenceCreate(BaseModel):
    """One reference attached to a new topic.

    A discriminated union over ``kind``: ``url`` requires ``url``,
    ``paste`` requires ``pasted_text``, ``upload`` requires
    ``file_name``+``content_type``+``file_b64``. The cross-field
    discriminator check lives in the ``@model_validator`` below.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["url", "paste", "upload"]
    title: str
    url: str | None = None
    pasted_text: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    file_b64: str | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be empty after stripping")
        if len(stripped) > 200:
            raise ValueError("title must be 200 characters or fewer")
        return stripped

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        lowered = value.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            raise ValueError("url must use the http or https scheme")
        return value

    @field_validator("content_type")
    @classmethod
    def _check_content_type(cls, value: str | None) -> str | None:
        if value is None:
            return value
        allowed = {
            "text/plain",
            "text/markdown",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if value not in allowed:
            raise ValueError(
                f"content_type must be one of {sorted(allowed)}; got {value!r}"
            )
        return value

    @field_validator("pasted_text")
    @classmethod
    def _check_pasted_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("pasted_text must not be empty after stripping")
        return value

    @field_validator("file_name", "file_b64")
    @classmethod
    def _check_non_empty_str(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("file_name/file_b64 must not be empty when supplied")
        return value

    @model_validator(mode="after")
    def _check_discriminator(self) -> "ReferenceCreate":
        """Per-kind required-field contract -- mirrors the SQL CHECK constraint."""

        if self.kind == "url":
            if not self.url:
                raise ValueError("url is required when kind='url'")
            if self.pasted_text is not None:
                raise ValueError("pasted_text is not allowed when kind='url'")
            if self.file_name is not None or self.content_type is not None or self.file_b64 is not None:
                raise ValueError("file fields are not allowed when kind='url'")
        elif self.kind == "paste":
            if not self.pasted_text:
                raise ValueError("pasted_text is required when kind='paste'")
            if self.url is not None:
                raise ValueError("url is not allowed when kind='paste'")
            if self.file_name is not None or self.content_type is not None or self.file_b64 is not None:
                raise ValueError("file fields are not allowed when kind='paste'")
        elif self.kind == "upload":
            if not self.file_name:
                raise ValueError("file_name is required when kind='upload'")
            if not self.content_type:
                raise ValueError("content_type is required when kind='upload'")
            if not self.file_b64:
                raise ValueError("file_b64 is required when kind='upload'")
            if self.url is not None or self.pasted_text is not None:
                raise ValueError("url/pasted_text are not allowed when kind='upload'")
            # SRA-902: validate the base64 payload at the Pydantic boundary so a
            # malformed request is a 422 client error rather than a partial-
            # success row that surfaces as "decode_failed" in the response.
            try:
                base64.b64decode(self.file_b64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(f"file_b64 is not valid base64: {exc}") from exc
        return self


class ReferenceOut(BaseModel):
    """Response shape for a persisted reference.

    ``extracted_text`` carries the (possibly truncated) text the
    fetcher/extractor produced; for paste references the same field
    mirrors the user-supplied ``pasted_text`` so the dashboard renders
    a single uniform view.
    """

    id: int
    kind: Literal["url", "paste", "upload"]
    title: str
    url: str | None = None
    pasted_text: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    extracted_text: str | None = None
    fetch_status: str
    fetch_error: str | None = None
    created_at: datetime


class TopicCreate(BaseModel):
    """POST /api/v1/topics request body.

    ``references`` must carry at least one entry (a topic with no
    references cannot be regenerated into a quiz -- enforced by the
    min_length=1 validator so the violation surfaces as 422 before
    any DB write).
    """

    model_config = ConfigDict(extra="forbid")

    topic: str
    subtopic: str | None = None
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    references: list[ReferenceCreate] = Field(min_length=1, max_length=10)

    @field_validator("topic")
    @classmethod
    def _check_topic(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("topic must not be empty after stripping")
        if len(stripped) > 200:
            raise ValueError("topic must be 200 characters or fewer")
        return stripped

    @field_validator("summary")
    @classmethod
    def _check_summary(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("summary must not be empty after stripping")
        if len(value) > 2_000:
            raise ValueError("summary must be 2000 characters or fewer")
        return value

    @field_validator("subtopic")
    @classmethod
    def _check_subtopic(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("subtopic must not be empty when supplied")
        if len(stripped) > 200:
            raise ValueError("subtopic must be 200 characters or fewer")
        return stripped

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        if len(value) > 16:
            raise ValueError("at most 16 tags may be supplied")
        cleaned: list[str] = []
        for tag in value:
            stripped = tag.strip()
            if not stripped:
                raise ValueError("tag entries must not be empty after stripping")
            if len(stripped) > 50:
                raise ValueError("tag entries must be 50 characters or fewer")
            cleaned.append(stripped)
        return cleaned


class TopicCreateResult(BaseModel):
    """POST /api/v1/topics response body.

    ``quiz_set_id`` and ``interview_plan_id`` contain the artifacts produced
    by Generate now. They remain nullable for partial success: reference
    ingestion is durable even if one generation pipeline fails, and ``errors``
    explains that failure to the dashboard.
    """

    entry: EntryOut
    references: list[ReferenceOut]
    quiz_set_id: int | None = None
    interview_plan_id: int | None = None
    errors: list[str] = Field(default_factory=list)


class TopicRegenerateResult(BaseModel):
    """POST /api/v1/topics/{entry_id}/regenerate response body.

    Carries the IDs of the freshly generated ``QuizSet`` and
    ``InterviewPlan`` plus any per-step error messages. ``generation_errors``
    is empty on the happy path; a non-empty list means at least one of
    the two pipelines surfaced a typed error (the call still returns 200
    so the dashboard can render partial-success). The fields are nullable
    so a pipeline that errored reports ``quiz_set_id=null`` /
    ``interview_plan_id=null`` rather than fabricating an ID.
    """

    quiz_set_id: int | None = None
    interview_plan_id: int | None = None
    generation_errors: list[str] = Field(default_factory=list)


class AssessmentAnalyzeRequest(TopicCreate):
    """Reference bundle used to prepare an on-demand assessment draft."""


class AssessmentGenerateRequest(BaseModel):
    """User-approved topics and per-session settings."""

    model_config = ConfigDict(extra="forbid")

    selected_topic_keys: list[str] = Field(min_length=1, max_length=12)
    mode: Literal["mcq", "interview", "both"]
    mcq_question_count: int = Field(default=5, ge=1, le=MAX_QUESTION_COUNT)
    interview_question_count: int = Field(
        default=3,
        ge=MIN_INTERVIEW_QUESTION_COUNT,
        le=MAX_INTERVIEW_QUESTION_COUNT,
    )

    @field_validator("selected_topic_keys")
    @classmethod
    def _unique_non_empty_topic_keys(cls, values: list[str]) -> list[str]:
        stripped = [value.strip() for value in values]
        if any(not value for value in stripped):
            raise ValueError("selected topic keys must not be empty")
        if len(set(stripped)) != len(stripped):
            raise ValueError("selected topic keys must be unique")
        return stripped
