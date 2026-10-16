"""
Data-access functions. Every function is scoped by `user_id` and
never trusts a caller-supplied row's ownership -- that discipline
matters more once Phase 3 adds real multi-user auth, but it costs
nothing to establish now while there's only one local user.
"""

from datetime import UTC, date, datetime, time
from typing import TypedDict

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, String, cast, func, or_, select

from sra.agents.schemas import MCQDraft
from sra.db.models import (
    QuizAnswer,
    QuizAnswerCreate,
    QuizAttempt,
    QuizQuestion,
    QuizQuestionCreate,
    QuizSet,
    QuizSetCreate,
    ReviewSchedule,
    StudyEntry,
    StudyEntryCreate,
    User,
    _utcnow,
)
from sra.db.scheduler import schedule_reviews_for_entry


class UserNotFoundError(Exception):
    """Raised when a repository helper targets a missing `users` row."""


class UserPreferences(TypedDict):
    """Canonical Phase 5 per-user preferences shape.

    Every later Phase 5 caller (`users_due_for_wakeup`, the voice quiz
    conversation graph, the pregeneration job) imports this typed dict
    instead of reaching into `User` columns individually.
    """

    preferred_review_time: str
    timezone: str
    quiz_question_count: int
    quiz_option_count: int
    wakeup_notification_sent_at: datetime | None


def get_user_preferences(user_id: int, session: Session) -> UserPreferences:
    """Return the typed preferences row for `user_id`.

    Raises ``UserNotFoundError`` when the row is missing so callers can
    distinguish a missing user from a present-but-empty preferences set
    (which never happens today but could if a future ticket narrows the
    default values).
    """

    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError(f"user_id {user_id} does not exist")
    return {
        "preferred_review_time": user.preferred_review_time,
        "timezone": user.timezone,
        "quiz_question_count": user.quiz_question_count,
        "quiz_option_count": user.quiz_option_count,
        "wakeup_notification_sent_at": user.wakeup_notification_sent_at,
    }


def update_user_preferences(
    user_id: int,
    prefs: dict,
    session: Session,
) -> UserPreferences:
    """Persist the caller-supplied preference fields and return the row.

    Only keys present in ``prefs`` are written; absent keys keep their
    current value. ``wakeup_notification_sent_at`` is intentionally NOT in
    the writable set -- it is server-owned state, written exclusively by
    the wake-up scheduler (SRA-502). A PUT that did clear it would race
    with the scheduler and suppress the next day's notification.
    """

    user = session.get(User, user_id)
    if user is None:
        raise UserNotFoundError(f"user_id {user_id} does not exist")

    if "preferred_review_time" in prefs:
        user.preferred_review_time = prefs["preferred_review_time"]
    if "timezone" in prefs:
        user.timezone = prefs["timezone"]
    if "quiz_question_count" in prefs:
        user.quiz_question_count = prefs["quiz_question_count"]
    if "quiz_option_count" in prefs:
        user.quiz_option_count = prefs["quiz_option_count"]

    session.add(user)
    session.commit()
    session.refresh(user)

    return {
        "preferred_review_time": user.preferred_review_time,
        "timezone": user.timezone,
        "quiz_question_count": user.quiz_question_count,
        "quiz_option_count": user.quiz_option_count,
        "wakeup_notification_sent_at": user.wakeup_notification_sent_at,
    }


def create_entry(*, entry_create: StudyEntryCreate, user_id: int, session: Session) -> StudyEntry:
    """
    Insert a StudyEntry scoped to user_id, then call
    scheduler.schedule_reviews_for_entry for it so every entry is
    born with its six review checkpoints already in place.

    Takes a StudyEntryCreate (not loose kwargs) so id/user_id/created_at
    can never be supplied by a caller -- see the convention note atop
    db/models.py.
    """

    row = StudyEntry.model_validate(entry_create, update={"user_id": user_id})

    session.add(row)
    session.commit()
    session.refresh(row)

    # refresh() above populates the autoincremented id from the DB.
    assert row.id is not None
    schedule_reviews_for_entry(row.id, row.created_at.date(), session)

    return row


def get_due_reviews(user_id: int, as_of: date, session: Session) -> list[ReviewSchedule]:
    """
    Return this user's pending ReviewSchedule rows with
    scheduled_date <= as_of, newest scheduled_date last so the most
    overdue items surface first when a caller reverses the list (or
    sort however you find most useful for a digest -- just be
    explicit about the order, since the API layer renders it as-is).

    Concept: the query behind both GET /api/v1/digest and (in Phase
    later) the query agent's due-reviews tool -- one function, two
    callers, so the definition of "due" never drifts between them.
    """

    statement = (
        select(ReviewSchedule)
        .join(StudyEntry)
        .where(
            StudyEntry.user_id == user_id,
            ReviewSchedule.status == "pending",
            ReviewSchedule.scheduled_date <= as_of,
        )
        .order_by(ReviewSchedule.scheduled_date)  # pyright: ignore[reportArgumentType] -- SQLModel field typed as `date`, not a column, at the class level
    )

    return list(session.exec(statement).all())


def search_entries(
    user_id: int,
    query: str,
    session: Session,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[StudyEntry]:
    """
    Case-insensitive substring match over topic/subtopic/tags/summary
    for this user only, optionally bounded by [date_from, date_to],
    newest first. An empty query returns all of this user's entries.

    Concept: build the SQL filter conditionally (append predicates
    only for the filters that were actually passed) rather than
    branching into several near-duplicate queries.
    """

    # SQLModel fields are typed as their plain Python type (str/datetime/...)
    # at the class level, so pyright can't see the InstrumentedAttribute that
    # actually exists at runtime -- ignores below match the one already on
    # get_due_reviews's order_by() for the same reason.
    conditions: list = [StudyEntry.user_id == user_id]  # pyright: ignore[reportArgumentType]

    if query:
        pattern = f"%{query}%"
        conditions.append(
            or_(
                StudyEntry.topic.ilike(pattern),  # pyright: ignore[reportAttributeAccessIssue]
                StudyEntry.subtopic.ilike(pattern),  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
                StudyEntry.summary.ilike(pattern),  # pyright: ignore[reportAttributeAccessIssue]
                cast(StudyEntry.tags, String).ilike(pattern),
            )
        )

    if date_from is not None:
        conditions.append(StudyEntry.created_at >= datetime.combine(date_from, time.min, tzinfo=UTC))

    if date_to is not None:
        conditions.append(StudyEntry.created_at <= datetime.combine(date_to, time.max, tzinfo=UTC))

    statement = (
        select(StudyEntry)
        .where(*conditions)
        .order_by(StudyEntry.created_at.desc())  # pyright: ignore[reportAttributeAccessIssue]
    )

    return list(session.exec(statement).all())


def list_upcoming_reviews(
    user_id: int,
    from_date: date,
    to_date: date,
    session: Session,
) -> list[ReviewSchedule]:
    """
    Return this user's pending ``ReviewSchedule`` rows whose ``scheduled_date``
    lies in the closed interval ``[from_date, to_date]`` and is strictly after
    "due today" -- so the calendar view doesn't double-count rows the digest
    endpoint already owns. Ordered by ``scheduled_date`` then ``id`` so identical
    dates render in the same order they were scheduled.

    Concept: a single SQL query for the entire window. The dashboard buckets
    the result in JavaScript, keeping the response shape flat and avoiding a
    custom JSON encoder for grouped data.
    """

    statement = (
        select(ReviewSchedule)
        .join(StudyEntry)
        .where(
            StudyEntry.user_id == user_id,  # pyright: ignore[reportArgumentType]
            ReviewSchedule.status == "pending",
            ReviewSchedule.scheduled_date >= from_date,  # pyright: ignore[reportArgumentType]
            ReviewSchedule.scheduled_date <= to_date,  # pyright: ignore[reportArgumentType]
        )
        .order_by(
            ReviewSchedule.scheduled_date,  # pyright: ignore[reportArgumentType]
            ReviewSchedule.id,  # pyright: ignore[reportArgumentType]
        )
    )
    return list(session.exec(statement).all())


def count_user_entries(user_id: int, session: Session) -> int:
    """Return the count of ``StudyEntry`` rows owned by ``user_id``.

    The dashboard header renders this as the "Total entries" tile. Implemented
    as a single ``SELECT count(*)`` against the indexed ``user_id`` column so
    it stays cheap even for users with thousands of captures.
    """

    statement = select(func.count(StudyEntry.id)).where(  # pyright: ignore[reportArgumentType]
        StudyEntry.user_id == user_id,
    )
    return int(session.exec(statement).one())


def mark_review_done(user_id: int, review_id: int, status: str, session: Session) -> ReviewSchedule:
    """
    Flip a review's status ("done" or "skipped") and stamp
    completed_at (use `datetime.now(timezone.utc)` -- this project's
    SQLModel setup rejects naive datetimes on write, see
    db/models.py's `_utcnow` helper). Raise a plain ValueError if
    review_id doesn't resolve to a review owned (via its parent
    StudyEntry) by user_id -- never silently no-op on a mismatched id.
    """

    statement = (
        select(ReviewSchedule)
        .join(StudyEntry)
        .where(
            StudyEntry.user_id == user_id,
            ReviewSchedule.id == review_id,
        )
    )
    review = session.exec(statement).first()

    if review is None:
        raise ValueError(f"review_id {review_id} does not belong to user_id {user_id}")

    review.status = status
    review.completed_at = datetime.now(UTC)

    session.add(review)
    session.commit()
    session.refresh(review)

    return review


def create_quiz_set(
    *,
    entry: StudyEntry,
    user_id: int,
    session: Session,
    generated_on: date,
    question_count: int,
    option_count: int,
    drafts: list[MCQDraft],
) -> QuizSet:
    """
    Insert a QuizSet scoped to user_id along with one QuizQuestion row per
    draft, all in one transaction.

    Server-owned fields (user_id, entry_id, generated_on, snapshot counts) are
    applied via `model_validate(QuizSetCreate(entry_id=entry.id),
    update={...})` so a caller can never override them through the Create
    schema. The `(entry_id, generated_on)` unique constraint makes the nightly
    pregeneration idempotent -- a duplicate call surfaces as a typed
    ValueError rather than a raw SQLAlchemy IntegrityError.
    """

    assert entry.id is not None
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": user_id,
            "entry_id": entry.id,
            "question_count": question_count,
            "option_count": option_count,
            "generated_on": generated_on,
        },
    )
    session.add(quiz_set)

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"quiz_set for entry_id={entry.id} on generated_on={generated_on} already exists"
        ) from exc

    assert quiz_set.id is not None

    for order_index, draft in enumerate(drafts):
        question = QuizQuestion.model_validate(
            QuizQuestionCreate(
                quiz_set_id=quiz_set.id,
                question_text=draft.question_text,
                options=list(draft.options),
                correct_option_index=draft.correct_option_index,
                explanation=draft.explanation,
                order_index=order_index,
            )
        )
        session.add(question)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"quiz_questions for quiz_set_id={quiz_set.id} violated a constraint"
        ) from exc

    session.refresh(quiz_set)
    question_rows = list(
        session.exec(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_set_id == quiz_set.id)
            .order_by(QuizQuestion.order_index)  # pyright: ignore[reportArgumentType]
        )
    )
    try:
        quiz_set.questions = question_rows  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        pass
    return quiz_set


def get_quiz_set(user_id: int, quiz_set_id: int, session: Session) -> QuizSet | None:
    """
    Fetch a QuizSet by id, scoped to user_id. Returns None when the set
    does not exist or belongs to another user -- never raises on a
    mismatched id, because the dashboard must silently skip another
    user's rows.
    """

    statement = select(QuizSet).where(
        QuizSet.id == quiz_set_id,  # pyright: ignore[reportArgumentType]
        QuizSet.user_id == user_id,  # pyright: ignore[reportArgumentType]
    )
    return session.exec(statement).first()


def list_today_quiz_sets(user_id: int, today: date, session: Session) -> list[QuizSet]:
    """
    Return this user's QuizSet rows with generated_on == today, ordered by
    entry_id so a stable ordering reaches the dashboard.
    """

    statement = (
        select(QuizSet)
        .where(
            QuizSet.user_id == user_id,  # pyright: ignore[reportArgumentType]
            QuizSet.generated_on == today,  # pyright: ignore[reportArgumentType]
        )
        .order_by(QuizSet.entry_id)  # pyright: ignore[reportArgumentType]
    )
    return list(session.exec(statement).all())


def has_quiz_set_for_entry_today(user_id: int, entry_id: int, today: date, session: Session) -> bool:
    """
    Return True when this user already has a QuizSet for (entry_id, today).

    Intended for the SRA-403 nightly pregeneration job to skip entries that
    are already covered; the unique constraint on
    ``(entry_id, generated_on)`` is the authoritative guard, but a precheck
    lets the job skip work without a wasted INSERT.
    """

    statement = select(QuizSet.id).where(
        QuizSet.user_id == user_id,  # pyright: ignore[reportArgumentType]
        QuizSet.entry_id == entry_id,  # pyright: ignore[reportArgumentType]
        QuizSet.generated_on == today,  # pyright: ignore[reportArgumentType]
    )
    return session.exec(statement).first() is not None


def save_quiz_answer(
    *,
    attempt: QuizAttempt,
    question: QuizQuestion,
    selected_option_index: int,
    is_correct: bool,
    session: Session,
) -> QuizAnswer:
    """
    Persist a QuizAnswer for one (attempt, question) pair, stamping
    answered_at to _utcnow(). The unique (attempt_id, question_id)
    constraint enforces one answer per question per attempt; a duplicate
    surfaces as a typed ValueError rather than a raw SQLAlchemy error.
    """

    answer = QuizAnswer.model_validate(
        QuizAnswerCreate(
            attempt_id=attempt.id if attempt.id is not None else 0,
            question_id=question.id if question.id is not None else 0,
            selected_option_index=selected_option_index,
            is_correct=is_correct,
        )
    )
    session.add(answer)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"answer for attempt_id={attempt.id} question_id={question.id} already exists"
        ) from exc
    session.refresh(answer)
    return answer


def complete_quiz_attempt(*, attempt: QuizAttempt, score: int, session: Session) -> QuizAttempt:
    """
    Stamp `attempt.completed_at` to a timezone-aware UTC now and persist
    `score`. Caller is responsible for the attempt already being a fresh,
    saved row (typical caller is the dashboard which just inserted it).
    """

    attempt.completed_at = _utcnow()
    attempt.score = score

    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def get_quiz_attempt(user_id: int, attempt_id: int, session: Session) -> QuizAttempt | None:
    """
    Fetch a QuizAttempt by id, scoped to user_id. Returns None when the
    attempt does not exist or belongs to another user.
    """

    statement = select(QuizAttempt).where(
        QuizAttempt.id == attempt_id,  # pyright: ignore[reportArgumentType]
        QuizAttempt.user_id == user_id,  # pyright: ignore[reportArgumentType]
    )
    return session.exec(statement).first()


def get_quiz_questions(quiz_set_id: int, session: Session) -> list[QuizQuestion]:
    """Return the quiz set's questions ordered by ``order_index`` ASC."""
    statement = (
        select(QuizQuestion)
        .where(QuizQuestion.quiz_set_id == quiz_set_id)
        .order_by(QuizQuestion.order_index)  # pyright: ignore[reportArgumentType]
    )
    return list(session.exec(statement).all())


def advance_quiz_cursor(*, attempt: QuizAttempt, session: Session) -> QuizAttempt:
    """Increment ``attempt.cursor`` by 1 and commit. Returns the refreshed attempt."""
    attempt.cursor = attempt.cursor + 1
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def _demo_user_dependents_minimum(user_id: int, session: Session) -> datetime | None:
    """Return the minimum related timestamp for a user, or None when no dependents are attached.

    Iterates every dependent table the SRA-603 cascade clears and returns the
    smallest timestamp among them. ``None`` means the user has no dependents and
    must not be returned by ``list_old_demo_users`` (SRA-603's spec).
    """
    candidates: list[datetime] = []

    entry_min = session.exec(
        select(func.min(StudyEntry.created_at)).where(StudyEntry.user_id == user_id)
    ).one()
    if entry_min is not None:
        candidates.append(entry_min)

    schedule_min = session.exec(
        select(func.min(ReviewSchedule.scheduled_date))
        .join(StudyEntry)
        .where(StudyEntry.user_id == user_id)
    ).one()
    if schedule_min is not None:
        # Convert the date column to a timezone-aware datetime at UTC midnight so
        # the comparison against ``as_of_utc - retention_days`` is consistent.
        candidates.append(datetime.combine(schedule_min, time.min, tzinfo=UTC))

    quiz_set_min = session.exec(
        select(func.min(QuizSet.generated_on)).where(QuizSet.user_id == user_id)
    ).one()
    if quiz_set_min is not None:
        candidates.append(datetime.combine(quiz_set_min, time.min, tzinfo=UTC))

    quiz_attempt_min = session.exec(
        select(func.min(QuizAttempt.started_at)).where(QuizAttempt.user_id == user_id)
    ).one()
    if quiz_attempt_min is not None:
        candidates.append(quiz_attempt_min)

    if not candidates:
        return None
    return min(candidates)


def list_old_demo_users(
    retention_days: int,
    as_of_utc: datetime,
    session: Session,
) -> list[tuple[User, datetime]]:
    """Return every demo user whose oldest dependent timestamp is older than the cutoff.

    A demo user with no dependents is never returned -- ``reset_demo_data`` keeps
    those rows intact so an OAuth user who never captured anything survives a
    cleanup. Each tuple pairs the user with the timestamp used to decide
    eligibility so callers can log or audit the cutoff.
    """
    cutoff = as_of_utc - timedelta(days=retention_days)
    rows: list[tuple[User, datetime]] = []
    for user in session.exec(
        select(User).where(User.__table__.c.is_demo_user.is_(True))  # type: ignore[attr-defined]
    ).all():
        assert user.id is not None
        oldest = _demo_user_dependents_minimum(user.id, session)
        if oldest is None or oldest > cutoff:
            continue
        rows.append((user, oldest))
    return rows


def delete_user_cascade(user: User, session: Session) -> None:
    """Delete ``user`` and every dependent row in FK-safe order.

    The cascade touches ``study_entries`` (which clears ``review_schedules``),
    ``quiz_sets`` (which clears ``quiz_questions``), and ``quiz_attempts``
    (which clears ``quiz_answers``). A detached/already-deleted user raises
    ``UserNotFoundError`` so callers can distinguish a successful delete from a
    no-op. The caller owns the surrounding transaction / commit.
    """
    if user.id is None or session.get(User, user.id) is None:
        raise UserNotFoundError(f"user {getattr(user, 'id', None)!r} is not present in this session")

    user_id = user.id

    # quiz_answers must die before quiz_attempts because the FK on
    # quiz_answers.attempt_id does not declare ondelete="CASCADE" in this
    # schema. With SQLite (the default dev/test engine) FK enforcement is
    # off by default, so leaving the answers in place leaves dangling rows
    # that survive the user delete. Explicit cascade keeps the schema
    # portable to Postgres regardless of session-level PRAGMA settings.
    attempt_ids: list[int] = []
    for attempt in session.exec(
        select(QuizAttempt).where(QuizAttempt.user_id == user_id)  # pyright: ignore[reportArgumentType]
    ).all():
        assert attempt.id is not None
        attempt_ids.append(attempt.id)
    if attempt_ids:
        for answer in session.exec(
            select(QuizAnswer).where(
                QuizAnswer.attempt_id.in_(attempt_ids)  # type: ignore[arg-type]
            )
        ).all():
            session.delete(answer)

    # quiz_attempts must die first so the quiz_sets->quiz_attempts link is
    # free when we clear quiz_sets.
    for attempt in session.exec(
        select(QuizAttempt).where(QuizAttempt.user_id == user_id)
    ).all():
        session.delete(attempt)

    # quiz_sets and the dependent quiz_questions row by row (no FK cascade from
    # the platform default; SQLAlchemy ORM cascade may not be configured for
    # quiz_set -> questions).
    for quiz_set in session.exec(
        select(QuizSet).where(QuizSet.user_id == user_id)
    ).all():
        for question in session.exec(
            select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
        ).all():
            session.delete(question)
        session.delete(quiz_set)

    # study_entries and the dependent review_schedules.
    for entry in session.exec(
        select(StudyEntry).where(StudyEntry.user_id == user_id)
    ).all():
        for review in session.exec(
            select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)
        ).all():
            session.delete(review)
        session.delete(entry)

    session.delete(user)
    session.flush()


from datetime import timedelta  # noqa: E402  -- late import keeps the SRA-601 surface above the cascade
