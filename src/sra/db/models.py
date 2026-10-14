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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from sqlalchemy import CheckConstraint, Enum, Integer, String, UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel


def _utcnow() -> datetime:
    """Timezone-aware "now" -- this SQLModel version rejects naive datetimes on write."""
    return datetime.now(UTC)


def _utcnow_date() -> date:
    """Today's date in UTC -- the default for QuizSet.generated_on."""
    return datetime.now(UTC).date()


MAX_QUESTION_COUNT = 20
MAX_OPTION_COUNT = 6

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
    )

    id: int | None = Field(default=None, primary_key=True)
    # Nullable only for preserved pre-OAuth local users. Every user created by
    # the Phase 3 OAuth path must have a non-empty, provider-verified subject.
    google_sub: str | None = Field(default=None, unique=True, index=True)
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
