"""
ORM models (SQLModel). Written out in full -- these aren't a learning
stub, since the schema itself is a design decision this project has
already made (see docs/design/technical-design.md section 2).

User carries the Phase 3 identity fields needed to map a verified Google
identity to a durable local row.  ``google_sub`` remains nullable only so a
database created before OAuth can be upgraded without losing its local user;
the verified-login path requires a non-empty subject.

Convention: any table a caller can create with user-influenced data
gets a `<Name>Base`/`<Name>Create` split, not just a single `table=True`
class. `<Name>Create` declares ONLY the fields a caller may legitimately
supply; server-owned fields (`id`, `created_at`, foreign keys assigned
by the repository layer, etc.) live exclusively on the `table=True`
class. Repository functions build the row with
`Model.model_validate(create_obj, update={...server-owned fields...})`
rather than re-listing fields as constructor kwargs -- that way a
server-owned field can never be supplied by a caller, because the
`Create` schema's type never carries it, not because whoever wrote the
call site remembered to leave it out. See StudyEntry/StudyEntryCreate
below and repository.create_entry for the reference example; follow
this shape for every new table.
"""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
from sqlalchemy import Boolean, CheckConstraint, Enum, Integer, String, UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel


def _utcnow() -> datetime:
    """Timezone-aware "now" -- this SQLModel version rejects naive datetimes on write."""
    return datetime.now(UTC)


def _utcnow_date() -> date:
    """Today's date in UTC -- the default for QuizSet.generated_on."""
    return datetime.now(UTC).date()


MAX_QUESTION_COUNT = 20
MAX_OPTION_COUNT = 6
MIN_INTERVIEW_QUESTION_COUNT = 3
MAX_INTERVIEW_QUESTION_COUNT = 12
MIN_INTERVIEW_TARGET_MINUTES = 1
MAX_INTERVIEW_TARGET_MINUTES = 20

_REVIEW_TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class UserRole(StrEnum):
    """Roles with observable Phase 3 authorization behavior."""

    USER = "user"
    ADMIN = "admin"


class UserBase(SQLModel):
    email: str = Field(unique=True)
    display_name: str
    preferred_review_time: str = Field(
        default="09:00",
        sa_column=Column(String, nullable=False, server_default="09:00"),
    )
    timezone: str = Field(
        default="UTC",
        sa_column=Column(String, nullable=False, server_default="UTC"),
    )
    quiz_question_count: int = Field(
        default=10,
        sa_column=Column(Integer, nullable=False, server_default="10"),
    )
    quiz_option_count: int = Field(
        default=4,
        sa_column=Column(Integer, nullable=False, server_default="4"),
    )
    interview_question_count: int = Field(
        default=6,
        sa_column=Column(Integer, nullable=False, server_default="6"),
    )
    interview_target_minutes: int = Field(
        default=5,
        sa_column=Column(Integer, nullable=False, server_default="5"),
    )
    audio_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    is_demo_user: bool = False  # SRA-601: marker used by the demo reset job (SRA-603)

    @field_validator("preferred_review_time")
    @classmethod
    def validate_preferred_review_time(cls, value: str) -> str:
        import re

        if re.fullmatch(_REVIEW_TIME_PATTERN, value) is None:
            raise ValueError(
                "preferred_review_time must be HH:MM with hour 00-23 and minute 00-59"
            )
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value

    @field_validator("quiz_question_count")
    @classmethod
    def validate_quiz_question_count(cls, value: int) -> int:
        if value < 1 or value > MAX_QUESTION_COUNT:
            raise ValueError(
                f"quiz_question_count must be between 1 and {MAX_QUESTION_COUNT}"
            )
        return value

    @field_validator("quiz_option_count")
    @classmethod
    def validate_quiz_option_count(cls, value: int) -> int:
        if value < 2 or value > MAX_OPTION_COUNT:
            raise ValueError(
                f"quiz_option_count must be between 2 and {MAX_OPTION_COUNT}"
            )
        return value

    @field_validator("interview_question_count")
    @classmethod
    def validate_interview_question_count(cls, value: int) -> int:
        if value < MIN_INTERVIEW_QUESTION_COUNT or value > MAX_INTERVIEW_QUESTION_COUNT:
            raise ValueError(
                f"interview_question_count must be between "
                f"{MIN_INTERVIEW_QUESTION_COUNT} and {MAX_INTERVIEW_QUESTION_COUNT}"
            )
        return value

    @field_validator("interview_target_minutes")
    @classmethod
    def validate_interview_target_minutes(cls, value: int) -> int:
        if value < MIN_INTERVIEW_TARGET_MINUTES or value > MAX_INTERVIEW_TARGET_MINUTES:
            raise ValueError(
                f"interview_target_minutes must be between "
                f"{MIN_INTERVIEW_TARGET_MINUTES} and {MAX_INTERVIEW_TARGET_MINUTES}"
            )
        return value


class UserCreate(UserBase):
    """What a caller may supply to create a User -- no id or created_at.

    Phase 3 callers (the OAuth callback) supply ``google_sub`` here too;
    keeping it on the ``Create`` schema mirrors the Phase 4
    ``QuizSet``/``QuizAttempt`` pattern where a denormalized ownership
    field is accepted from the caller because it is part of the verified
    input, not a server-assigned foreign key.
    """

    google_sub: str | None = None

    model_config = {"extra": "forbid"}


class User(UserBase, table=True):
    __tablename__ = "users"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy's declared_attr typing, not a real error
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
        CheckConstraint(
            "google_sub IS NULL OR length(trim(google_sub)) > 0",
            name="ck_users_google_sub_nonempty",
        ),
        CheckConstraint(
            "preferred_review_time REGEXP '^[0-2][0-9]:[0-5][0-9]$'",
            name="ck_users_preferred_review_time_shape",
        ),
        CheckConstraint(
            "quiz_question_count BETWEEN 1 AND 20 AND quiz_option_count BETWEEN 2 AND 6",
            name="ck_users_quiz_counts_positive",
        ),
        CheckConstraint(
            "interview_question_count BETWEEN 3 AND 12 AND interview_target_minutes BETWEEN 1 AND 20",
            name="ck_users_interview_preferences_in_range",
        ),
        CheckConstraint(
            "is_demo_user IN (0, 1)",
            name="ck_users_is_demo_user_boolean",
        ),
        CheckConstraint(
            "audio_enabled IN (0, 1)",
            name="ck_users_audio_enabled_boolean",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    # Nullable only for preserved pre-OAuth local users. Every user created by
    # the Phase 3 OAuth path must have a non-empty, provider-verified subject.
    google_sub: str | None = Field(default=None, unique=True, index=True)
    # Override the inherited Pydantic default with an explicit SQLAlchemy
    # column so ``SQLModel.metadata.create_all`` (used by tests and the
    # first-boot migration) emits a ``DEFAULT 0`` server-side default. Without
    # this, raw-SQL inserts that omit ``is_demo_user`` would fail the NOT NULL
    # constraint even though the Python default is ``False``.
    is_demo_user: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )
    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(
            Enum(
                UserRole,
                values_callable=lambda roles: [role.value for role in roles],
                native_enum=False,
            ),
            nullable=False,
            server_default=UserRole.USER.value,
        ),
    )
    last_login_at: datetime | None = None
    wakeup_notification_sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("google_sub")
    @classmethod
    def validate_google_sub(cls, value: str | None) -> str | None:
        """Allow a legacy NULL, but never an empty external identity key."""
        if value is not None and not value.strip():
            raise ValueError("google_sub must be non-empty when supplied")
        return value


class StudyEntryBase(SQLModel):
    topic: str
    subtopic: str | None = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    summary: str
    raw_transcript: str


class StudyEntryCreate(StudyEntryBase):
    """What a caller may supply to create a StudyEntry -- no id, user_id, or created_at."""

    model_config = {"extra": "forbid"}


class StudyEntry(StudyEntryBase, table=True):
    __tablename__ = "study_entries"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class ReviewScheduleBase(SQLModel):
    entry_id: int = Field(foreign_key="study_entries.id", index=True)
    interval_label: str
    scheduled_date: date = Field(index=True)


class ReviewScheduleCreate(ReviewScheduleBase):
    """What a caller may supply to create a ReviewSchedule -- no id, status, or completed_at."""

    model_config = {"extra": "forbid"}


class ReviewSchedule(ReviewScheduleBase, table=True):
    __tablename__ = "review_schedules"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(default="pending")
    completed_at: datetime | None = None


class QuizSetBase(SQLModel):
    entry_id: int = Field(foreign_key="study_entries.id")


class QuizSetCreate(QuizSetBase):
    """What a caller may supply to create a QuizSet -- no id, user_id, generated_on, or snapshot counts."""

    model_config = {"extra": "forbid"}


class QuizSet(QuizSetBase, table=True):
    __tablename__ = "quiz_sets"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("entry_id", "generated_on", name="uq_quiz_sets_entry_generated_on"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    generated_on: date = Field(default_factory=_utcnow_date)
    question_count: int
    option_count: int
    discarded_for_evidence: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )


class QuizQuestionBase(SQLModel):
    quiz_set_id: int = Field(foreign_key="quiz_sets.id", index=True)
    question_text: str
    options: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    correct_option_index: int
    explanation: str | None = None
    order_index: int


class QuizQuestionCreate(QuizQuestionBase):
    """What a caller may supply to create a QuizQuestion -- no id."""

    model_config = {"extra": "forbid"}


class QuizQuestion(QuizQuestionBase, table=True):
    __tablename__ = "quiz_questions"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    bloom_level: str | None = Field(default=None, nullable=True)
    source_passage_quote: str | None = Field(default=None, nullable=True)
    distractor_misconception: str | None = Field(default=None, nullable=True)


class QuizAttemptBase(SQLModel):
    quiz_set_id: int = Field(foreign_key="quiz_sets.id")
    user_id: int = Field(foreign_key="users.id", index=True)
    mode: str = "web"
    total_questions: int


class QuizAttemptCreate(QuizAttemptBase):
    """What a caller may supply to create a QuizAttempt -- no id, started_at, completed_at, or score."""

    model_config = {"extra": "forbid"}


class QuizAttempt(QuizAttemptBase, table=True):
    __tablename__ = "quiz_attempts"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    score: int | None = None
    cursor: int = 0
    cursor_on_plan: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))


class QuizAnswerBase(SQLModel):
    attempt_id: int = Field(foreign_key="quiz_attempts.id", index=True)
    question_id: int = Field(foreign_key="quiz_questions.id")
    selected_option_index: int | None = None
    is_correct: bool


class QuizAnswerCreate(QuizAnswerBase):
    """What a caller may supply to create a QuizAnswer -- no id or answered_at."""

    model_config = {"extra": "forbid"}


class QuizAnswer(QuizAnswerBase, table=True):
    __tablename__ = "quiz_answers"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_quiz_answers_attempt_question"),
    )

    id: int | None = Field(default=None, primary_key=True)
    answered_at: datetime = Field(default_factory=_utcnow)


class InterviewPlanBase(SQLModel):
    quiz_set_id: int = Field(foreign_key="quiz_sets.id", unique=True)
    outline: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))
    target_questions: int


class InterviewPlanCreate(InterviewPlanBase):
    """What a caller may supply to create an InterviewPlan -- no id or created_at."""

    model_config = {"extra": "forbid"}


class InterviewPlan(InterviewPlanBase, table=True):
    __tablename__ = "interview_plans"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow)


class InterviewTurnRole(StrEnum):
    """String roles persisted on ``InterviewTurn`` rows.

    Mirrors the ``ck_interview_turns_role`` CHECK constraint on the
    ``interview_turns`` table. The repository helper validates every
    insert against this enum so a typo can never produce a row that
    fails the constraint on commit.
    """

    AI = "ai"
    USER = "user"
    SYSTEM_SUMMARY = "system_summary"


class InterviewTurnBase(SQLModel):
    attempt_id: int = Field(foreign_key="quiz_attempts.id", index=True)
    role: str
    content: str
    score: int | None = None
    outline_index: int | None = None
    rationale: str | None = None


class InterviewTurnCreate(InterviewTurnBase):
    """What a caller may supply to create an InterviewTurn -- no id or created_at."""

    model_config = {"extra": "forbid"}


class InterviewTurn(InterviewTurnBase, table=True):
    __tablename__ = "interview_turns"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        CheckConstraint(
            "role IN ('ai', 'user', 'system_summary')",
            name="ck_interview_turns_role",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow)

class ReferenceMaterialBase(SQLModel):
    """Common fields for every ``reference_materials`` row.

    The Python-side ``kind`` literal mirrors the user-visible API values
    (``"url"``, ``"paste"``, ``"upload"``) and the CHECK constraint in
    ``ck_reference_materials_kind`` -- see the ticket and the Phase 9
    design plan for the discriminator rules. ``title`` is a short
    user-supplied label (1-200 chars after stripping). The
    kind-specific payload fields (``url``, ``pasted_text``, ``file_name``,
    ``content_type``) live here too so the Create schema can validate
    them before any DB write.
    """

    kind: Literal["url", "paste", "upload"]
    title: str
    url: str | None = None
    pasted_text: str | None = None
    file_name: str | None = None
    content_type: str | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be empty after stripping")
        if len(stripped) > 200:
            raise ValueError("title must be 200 characters or fewer")
        return stripped


class ReferenceMaterialCreate(ReferenceMaterialBase):
    """What a caller may supply to create a ``ReferenceMaterial`` -- no id or created_at.

    Adds ``file_b64`` (the base64-encoded upload payload -- never
    persisted on the row, decoded and discarded by the extractor) and
    the per-kind discriminator validator. ``model_config.extra="forbid"``
    pins the Create shape so a typo at the call site fails fast.
    """

    file_b64: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("url")
    @classmethod
    def _check_url_scheme(cls, value: str | None) -> str | None:
        if value is None:
            return value
        lowered = value.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            raise ValueError("url must use the http or https scheme")
        return value

    @field_validator("pasted_text")
    @classmethod
    def _check_pasted_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("pasted_text must not be empty after stripping")
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

    @field_validator("file_name")
    @classmethod
    def _check_file_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("file_name must not be empty after stripping")
        return value

    @field_validator("file_b64")
    @classmethod
    def _check_file_b64(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("file_b64 must not be empty after stripping")
        return value

    @field_validator("kind", mode="before")
    @classmethod
    def _check_kind_literal(cls, value: object) -> object:
        if value not in ("url", "paste", "upload"):
            raise ValueError("kind must be one of 'url', 'paste', 'upload'")
        return value

    @model_validator(mode="after")
    def _check_discriminator(self) -> "ReferenceMaterialCreate":
        """Enforce the per-kind required-field contract at the Python layer.

        Mirrors the SQL ``ck_reference_materials_kind`` constraint so a
        violation surfaces as a typed ``ValidationError`` before any DB
        write -- the route can return 422 without round-tripping a
        CHECK failure.
        """

        kind = self.kind
        if kind == "url":
            if not self.url:
                raise ValueError("url is required when kind='url'")
            if self.pasted_text is not None:
                raise ValueError("pasted_text is not allowed when kind='url'")
            if self.file_name is not None or self.content_type is not None or self.file_b64 is not None:
                raise ValueError("file fields are not allowed when kind='url'")
        elif kind == "paste":
            if not self.pasted_text:
                raise ValueError("pasted_text is required when kind='paste'")
            if self.url is not None:
                raise ValueError("url is not allowed when kind='paste'")
            if self.file_name is not None or self.content_type is not None or self.file_b64 is not None:
                raise ValueError("file fields are not allowed when kind='paste'")
        elif kind == "upload":
            if not self.file_name:
                raise ValueError("file_name is required when kind='upload'")
            if not self.content_type:
                raise ValueError("content_type is required when kind='upload'")
            if not self.file_b64:
                raise ValueError("file_b64 is required when kind='upload'")
            if self.url is not None or self.pasted_text is not None:
                raise ValueError("url/pasted_text are not allowed when kind='upload'")
        return self


class ReferenceMaterial(ReferenceMaterialBase, table=True):
    """Phase 9 reference material attached to a ``StudyEntry``.

    The 3-way CHECK constraint enforces the discriminator at the SQL
    layer regardless of how the row was inserted (Pydantic-validated
    path, raw SQL, or a future caller). The ``entry_id`` FK is declared
    with ``ondelete="CASCADE"`` so the database engine itself can clear
    rows on the parent's death; ``delete_user_cascade`` mirrors this
    in Python for SQLite test runs where FK enforcement is disabled.
    """

    __tablename__ = "reference_materials"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        CheckConstraint(
            "(kind = 'url' AND url IS NOT NULL AND pasted_text IS NULL AND file_name IS NULL) "
            "OR (kind = 'paste' AND pasted_text IS NOT NULL AND url IS NULL AND file_name IS NULL) "
            "OR (kind = 'upload' AND file_name IS NOT NULL AND content_type IS NOT NULL "
            "    AND url IS NULL AND pasted_text IS NULL)",
            name="ck_reference_materials_kind",
        ),
    )

    entry_id: int = Field(foreign_key="study_entries.id", index=True)
    extracted_text: str | None = None
    fetch_status: str = Field(
        default="pending",
        sa_column=Column(String, nullable=False, server_default="pending"),
    )
    fetch_error: str | None = None
    fetched_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    id: int | None = Field(default=None, primary_key=True)
    # Override the inherited ``kind`` with an explicit SQLAlchemy column so
    # SQLModel doesn't try to map a ``Literal[...]`` Python type to a
    # SQLAlchemy type. Mirrors the ``User.role`` idiom above.
    kind: Literal["url", "paste", "upload"] = Field(
        sa_column=Column(String, nullable=False),
    )


class AssessmentSessionBase(SQLModel):
    """User-selected configuration for one on-demand assessment."""

    topic: str
    suggestions: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    selected_topic_keys: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    mode: str | None = None
    mcq_question_count: int = 5
    interview_question_count: int = 3


class AssessmentSessionCreate(AssessmentSessionBase):
    """Caller-controlled fields; ownership, artifacts, and results are server-owned."""

    model_config = {"extra": "forbid"}


class AssessmentSession(AssessmentSessionBase, table=True):
    __tablename__ = "assessment_sessions"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        CheckConstraint(
            "mode IS NULL OR mode IN ('mcq', 'interview', 'both')",
            name="ck_assessment_sessions_mode",
        ),
        CheckConstraint(
            "mcq_question_count BETWEEN 1 AND 20 AND interview_question_count BETWEEN 3 AND 12",
            name="ck_assessment_sessions_counts",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    entry_id: int = Field(foreign_key="study_entries.id", index=True)
    status: str = Field(default="draft", sa_column=Column(String, nullable=False, server_default="draft"))
    quiz_set_id: int | None = Field(default=None, foreign_key="quiz_sets.id")
    interview_plan_id: int | None = Field(default=None, foreign_key="interview_plans.id")
    mcq_attempt_id: int | None = Field(default=None, foreign_key="quiz_attempts.id")
    interview_attempt_id: int | None = Field(default=None, foreign_key="quiz_attempts.id")
    report: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    report_version: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default="1"))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
