"""
ORM models (SQLModel). Written out in full -- these aren't a learning
stub, since the schema itself is a design decision this project has
already made (see docs/design/technical-design.md section 2).

Phase 1 keeps User deliberately minimal (no role/OAuth/preference
fields yet) -- those get added in later phases as the features that
need them land, rather than speculatively now.

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

from sqlmodel import JSON, Column, Field, SQLModel


def _utcnow() -> datetime:
    """Timezone-aware "now" -- this SQLModel version rejects naive datetimes on write."""
    return datetime.now(UTC)


class UserBase(SQLModel):
    email: str = Field(unique=True)
    display_name: str


class UserCreate(UserBase):
    """What a caller may supply to create a User -- no id or created_at."""

    model_config = {"extra": "forbid"}


class User(UserBase, table=True):
    __tablename__ = "users"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy's declared_attr typing, not a real error

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow)


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
