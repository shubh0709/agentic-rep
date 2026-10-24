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

from sra.agents.interview_planner import InterviewOutline
from sra.agents.schemas import MCQDraft
from sra.db.models import (
    AssessmentSession,
    InterviewPlan,
    InterviewPlanCreate,
    InterviewTurn,
    InterviewTurnCreate,
    InterviewTurnRole,
    QuizAnswer,
    QuizAnswerCreate,
    QuizAttempt,
    QuizQuestion,
    QuizQuestionCreate,
    QuizSet,
    QuizSetCreate,
    ReferenceMaterial,
    ReviewSchedule,
    StudyEntry,
    StudyEntryCreate,
    User,
    _utcnow,
)
from sra.db.scheduler import compute_review_dates, schedule_reviews_for_entry


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
    interview_question_count: int
    interview_target_minutes: int
    audio_enabled: bool
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
        "interview_question_count": user.interview_question_count,
        "interview_target_minutes": user.interview_target_minutes,
        "audio_enabled": user.audio_enabled,
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
    if "interview_question_count" in prefs:
        user.interview_question_count = prefs["interview_question_count"]
    if "interview_target_minutes" in prefs:
        user.interview_target_minutes = prefs["interview_target_minutes"]
    if "audio_enabled" in prefs:
        user.audio_enabled = prefs["audio_enabled"]

    session.add(user)
    session.commit()
    session.refresh(user)

    return {
        "preferred_review_time": user.preferred_review_time,
        "timezone": user.timezone,
        "quiz_question_count": user.quiz_question_count,
        "quiz_option_count": user.quiz_option_count,
        "interview_question_count": user.interview_question_count,
        "interview_target_minutes": user.interview_target_minutes,
        "audio_enabled": user.audio_enabled,
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


def update_entry(
    entry: StudyEntry,
    *,
    topic: str | None,
    created_at: datetime | None,
    session: Session,
) -> StudyEntry:
    """Update an entry and keep pending review dates relative to its capture date."""

    if topic is not None:
        entry.topic = topic
    if created_at is not None:
        entry.created_at = created_at
        dates = dict(compute_review_dates(created_at.date()))
        reviews = session.exec(
            select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)
        ).all()
        for review in reviews:
            if review.status == "pending":
                review.scheduled_date = dates[review.interval_label]
                session.add(review)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def delete_entry_cascade(entry: StudyEntry, session: Session) -> None:
    """Delete one entry and its complete study/assessment dependency graph."""

    assert entry.id is not None
    quiz_sets = session.exec(select(QuizSet).where(QuizSet.entry_id == entry.id)).all()
    quiz_set_ids = [row.id for row in quiz_sets if row.id is not None]
    attempts = (
        session.exec(
            select(QuizAttempt).where(
                QuizAttempt.quiz_set_id.in_(quiz_set_ids)  # type: ignore[attr-defined]
            )
        ).all()
        if quiz_set_ids else []
    )
    attempt_ids = [row.id for row in attempts if row.id is not None]

    for assessment in session.exec(
        select(AssessmentSession).where(AssessmentSession.entry_id == entry.id)
    ).all():
        session.delete(assessment)
    if attempt_ids:
        for answer in session.exec(
            select(QuizAnswer).where(
                QuizAnswer.attempt_id.in_(attempt_ids)  # type: ignore[attr-defined]
            )
        ).all():
            session.delete(answer)
        for turn in session.exec(
            select(InterviewTurn).where(
                InterviewTurn.attempt_id.in_(attempt_ids)  # type: ignore[attr-defined]
            )
        ).all():
            session.delete(turn)
    for attempt in attempts:
        session.delete(attempt)
    for quiz_set in quiz_sets:
        for question in session.exec(
            select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
        ).all():
            session.delete(question)
        for plan in session.exec(
            select(InterviewPlan).where(InterviewPlan.quiz_set_id == quiz_set.id)
        ).all():
            session.delete(plan)
        session.delete(quiz_set)
    for review in session.exec(
        select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)
    ).all():
        session.delete(review)
    for reference in session.exec(
        select(ReferenceMaterial).where(ReferenceMaterial.entry_id == entry.id)
    ).all():
        session.delete(reference)
    session.delete(entry)
    session.commit()


def add_reference(
    *,
    entry_id: int,
    kind: str,
    title: str,
    session: Session,
    url: str | None = None,
    pasted_text: str | None = None,
    file_name: str | None = None,
    content_type: str | None = None,
    extracted_text: str | None = None,
    fetch_status: str = "pending",
    fetch_error: str | None = None,
    fetched_at: datetime | None = None,
) -> ReferenceMaterial:
    """Insert one ``ReferenceMaterial`` row for ``entry_id``.

    Validates the kind-discriminator at the Python layer (mirrors the
    SQL ``ck_reference_materials_kind`` CHECK constraint) and stamps
    ``fetch_status``/``fetch_error``/``fetched_at`` from the caller --
    the URL fetcher and document extractor populate those values
    before this helper runs, so a failed fetch ends up persisted as a
    ``fetched_text=None`` row with ``fetch_status='failed'`` and a
    short ``fetch_error`` string.
    """

    if kind not in ("url", "paste", "upload"):
        raise ValueError(f"kind {kind!r} is not one of 'url', 'paste', 'upload'")
    if not title or not title.strip():
        raise ValueError("title must not be empty")
    if kind == "url" and not url:
        raise ValueError("url is required when kind='url'")
    if kind == "paste" and (pasted_text is None or not pasted_text.strip()):
        raise ValueError("pasted_text is required when kind='paste'")
    if kind == "upload":
        if not file_name or not file_name.strip():
            raise ValueError("file_name is required when kind='upload'")
        if not content_type or not content_type.strip():
            raise ValueError("content_type is required when kind='upload'")

    resolved_fetched_at = fetched_at
    if resolved_fetched_at is None and fetch_status != "pending":
        resolved_fetched_at = _utcnow()

    row = ReferenceMaterial.model_validate(
        {
            "kind": kind,
            "title": title.strip(),
            "url": url,
            "pasted_text": pasted_text,
            "file_name": file_name,
            "content_type": content_type,
        },
        update={
            "entry_id": entry_id,
            "extracted_text": extracted_text,
            "fetch_status": fetch_status,
            "fetch_error": fetch_error,
            "fetched_at": resolved_fetched_at,
        },
    )

    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"reference for entry_id={entry_id} (kind={kind}) violated a constraint: {exc}"
        ) from exc
    session.refresh(row)
    return row


def list_references_for_entry(entry_id: int, *, session: Session) -> list[ReferenceMaterial]:
    """Return this entry's ``ReferenceMaterial`` rows ordered by ``created_at`` ASC.

    Stable ordering matters for the SRA-902 dashboard's reference list
    and the SRA-903 prompt bundle, both of which iterate the rows in
    creation order. An entry with no references returns ``[]``.
    """

    statement = (
        select(ReferenceMaterial)
        .where(ReferenceMaterial.entry_id == entry_id)  # pyright: ignore[reportArgumentType]
        .order_by(ReferenceMaterial.created_at, ReferenceMaterial.id)  # pyright: ignore[reportArgumentType]
    )
    return list(session.exec(statement).all())


def get_reference_text_bundle(
    entry_id: int,
    *,
    session: Session,
    max_chars_total: int = 12_000,
    max_chars_per_ref: int = 6_000,
) -> list[tuple[str, str]]:
    """Build the truncated per-reference text bundle for the SRA-903 quiz prompt.

    Concatenates ``extracted_text`` for ``url``/``upload`` rows and
    ``pasted_text`` for ``paste`` rows, each capped at
    ``max_chars_per_ref`` characters. When the running total exceeds
    ``max_chars_total``, later references are themselves truncated and
    tagged with the standard marker so the model knows the bounds.
    The marker format -- ``[... truncated at N chars ...]`` -- is pinned
    by the test so the SRA-903 prompt can rely on a stable string.
    """

    rows = list_references_for_entry(entry_id, session=session)
    bundle: list[tuple[str, str]] = []
    remaining_total = max_chars_total
    for row in rows:
        if remaining_total <= 0:
            break
        text = row.extracted_text if row.kind != "paste" else row.pasted_text
        text = text or ""
        if len(text) > max_chars_per_ref:
            text = text[:max_chars_per_ref]
            text = text + f"[... truncated at {max_chars_per_ref} chars ...]"
        if len(text) > remaining_total:
            text = text[:remaining_total]
            text = text + f"[... truncated at {remaining_total} chars ...]"
            remaining_total = 0
        else:
            remaining_total -= len(text)
        bundle.append((row.title, text))
    return bundle


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
    discarded_for_evidence: list[str] | None = None,
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

    ``discarded_for_evidence`` is the SRA-903 verify-pass discard log
    ("draft N: <reason>" strings). The default ``None`` resolves to an empty
    list so the column is always populated -- legacy callers keep their
    "all drafts accepted" semantics without having to opt in.
    """

    assert entry.id is not None
    discarded = list(discarded_for_evidence) if discarded_for_evidence is not None else []
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": user_id,
            "entry_id": entry.id,
            "question_count": question_count,
            "option_count": option_count,
            "generated_on": generated_on,
            "discarded_for_evidence": discarded,
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
            ),
            update={
                "bloom_level": getattr(draft, "bloom_level", None),
                "source_passage_quote": getattr(draft, "source_passage_quote", None),
                "distractor_misconception": getattr(draft, "distractor_misconception", None),
            },
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


def replace_quiz_set_questions(
    *,
    quiz_set: QuizSet,
    session: Session,
    question_count: int,
    option_count: int,
    drafts: list[MCQDraft],
    discarded_for_evidence: list[str] | None = None,
) -> QuizSet:
    """Replace a generated set's questions without replacing its parent row.

    Regeneration must retain the ``QuizSet`` identity because attempts refer to
    it. Answered questions are deliberately protected: changing or deleting
    them would rewrite the meaning of a learner's recorded answers.
    """
    assert quiz_set.id is not None

    existing_questions = list(
        session.exec(
            select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
        ).all()
    )
    existing_question_ids = [row.id for row in existing_questions if row.id is not None]
    if existing_question_ids:
        answered = session.exec(
            select(QuizAnswer.id).where(
                QuizAnswer.question_id.in_(existing_question_ids)  # type: ignore[union-attr]
            )
        ).first()
        if answered is not None:
            raise ValueError("cannot regenerate a quiz after an answer has been recorded")

    for question in existing_questions:
        session.delete(question)
    session.flush()

    quiz_set.question_count = question_count
    quiz_set.option_count = option_count
    quiz_set.discarded_for_evidence = list(discarded_for_evidence or [])
    session.add(quiz_set)

    for order_index, draft in enumerate(drafts):
        question = QuizQuestion.model_validate(
            QuizQuestionCreate(
                quiz_set_id=quiz_set.id,
                question_text=draft.question_text,
                options=list(draft.options),
                correct_option_index=draft.correct_option_index,
                explanation=draft.explanation,
                order_index=order_index,
            ),
            update={
                "bloom_level": getattr(draft, "bloom_level", None),
                "source_passage_quote": getattr(draft, "source_passage_quote", None),
                "distractor_misconception": getattr(draft, "distractor_misconception", None),
            },
        )
        session.add(question)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"replacement questions for quiz_set_id={quiz_set.id} violated a constraint"
        ) from exc

    session.refresh(quiz_set)
    question_rows = list(
        session.exec(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_set_id == quiz_set.id)
            .order_by(QuizQuestion.order_index)  # pyright: ignore[reportArgumentType]
        ).all()
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


def create_interview_plan(
    *,
    quiz_set: QuizSet,
    outline: InterviewOutline | list[dict],
    target_questions: int,
    session: Session,
) -> InterviewPlan | None:
    """Insert an ``InterviewPlan`` for ``quiz_set`` or return the existing row.

    The unique ``(quiz_set_id)`` constraint makes the nightly pregen
    job idempotent — a second invocation the same day races with the
    first one and the loser reads the winner's row instead of
    duplicating work.

    Accepts either a typed ``InterviewOutline`` (SRA-802 planner) or a
    plain ``list[dict]`` (SRA-804 unit tests + lazy-create path). Returns
    ``None`` when the outline is empty so the caller can count the
    entry as ``interview_plans_errored`` without persisting a degenerate
    plan. The outline is stored as the JSON shape the SRA-803
    conversation graph already knows how to consume
    (``[{"subtopic", "difficulty", "focus_prompt"}, ...]``).
    """
    assert quiz_set.id is not None

    if isinstance(outline, InterviewOutline):
        payload = [item.model_dump() for item in outline.items]
    else:
        payload = [dict(item) for item in outline]
    if not payload:
        return None

    plan = InterviewPlan.model_validate(
        InterviewPlanCreate(
            quiz_set_id=quiz_set.id,
            outline=payload,
            target_questions=target_questions,
        )
    )
    session.add(plan)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        statement = select(InterviewPlan).where(
            InterviewPlan.quiz_set_id == quiz_set.id,  # pyright: ignore[reportArgumentType]
        )
        existing = session.exec(statement).first()
        if existing is not None:
            return existing
        raise ValueError(
            f"interview_plan for quiz_set_id={quiz_set.id} conflict could not be resolved"
        ) from exc
    session.refresh(plan)
    return plan


def replace_interview_plan(
    *,
    quiz_set: QuizSet,
    outline: InterviewOutline | list[dict],
    target_questions: int,
    session: Session,
) -> InterviewPlan | None:
    """Replace an existing plan's outline in place, creating it when absent."""
    assert quiz_set.id is not None
    if isinstance(outline, InterviewOutline):
        payload = [item.model_dump() for item in outline.items]
    else:
        payload = [dict(item) for item in outline]
    if not payload:
        return None

    plan = session.exec(
        select(InterviewPlan).where(InterviewPlan.quiz_set_id == quiz_set.id)
    ).first()
    if plan is None:
        return create_interview_plan(
            quiz_set=quiz_set,
            outline=payload,
            target_questions=target_questions,
            session=session,
        )

    plan.outline = payload
    plan.target_questions = target_questions
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def get_or_create_interview_plan(
    quiz_set_id: int, session: Session
) -> tuple[QuizSet, InterviewPlan]:
    """Return ``(QuizSet, InterviewPlan)`` for ``quiz_set_id``, creating a placeholder when missing.

    Raises ``ValueError`` when the parent ``QuizSet`` is missing so the
    caller can distinguish a 404-style "no quiz set yet" from the
    happy path. When the ``InterviewPlan`` row is missing, persists a
    single-item placeholder outline (``subtopic="general"``,
    ``difficulty="medium"``, ``focus_prompt=entry.summary[:240]``) so
    the SRA-803 conversation graph always has something to expand;
    production pregeneration (SRA-802) replaces it with the real
    outline on the next pregen run.
    """
    quiz_set = session.get(QuizSet, quiz_set_id)
    if quiz_set is None:
        raise ValueError(f"quiz_set_id {quiz_set_id} does not exist")
    assert quiz_set.id is not None

    statement = select(InterviewPlan).where(
        InterviewPlan.quiz_set_id == quiz_set.id,  # pyright: ignore[reportArgumentType]
    )
    existing = session.exec(statement).first()
    if existing is not None:
        return quiz_set, existing

    entry = session.get(StudyEntry, quiz_set.entry_id) if quiz_set.entry_id else None
    focus_prompt = (entry.summary[:240] if entry is not None and entry.summary else "general recap")
    placeholder = [
        {"subtopic": "general", "difficulty": "medium", "focus_prompt": focus_prompt},
    ]
    plan = InterviewPlan.model_validate(
        InterviewPlanCreate(
            quiz_set_id=quiz_set.id,
            outline=placeholder,
            target_questions=1,
        )
    )
    session.add(plan)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(statement).first()
        if existing is not None:
            return quiz_set, existing
        raise
    session.refresh(plan)
    return quiz_set, plan


def append_interview_turn(
    *,
    attempt: QuizAttempt,
    role: str,
    content: str,
    score: int | None = None,
    outline_index: int | None = None,
    rationale: str | None = None,
    session: Session,
) -> InterviewTurn:
    """Append one ``InterviewTurn`` row for ``attempt`` and return the persisted row.

    Validates ``role`` against :class:`InterviewTurnRole` so a typo can
    never produce a row that fails the ``ck_interview_turns_role``
    CHECK constraint on commit. ``content`` is stripped; an empty
    value after stripping raises ``ValueError``. ``created_at`` defaults
    to ``_utcnow()``.
    """
    if role not in InterviewTurnRole:
        raise ValueError(
            f"role {role!r} is not a valid InterviewTurnRole; expected one of {[r.value for r in InterviewTurnRole]}"
        )
    stripped = content.strip()
    if not stripped:
        raise ValueError("content must be non-empty after stripping")

    assert attempt.id is not None
    turn = InterviewTurn.model_validate(
        InterviewTurnCreate(
            attempt_id=attempt.id,
            role=role,
            content=stripped,
            score=score,
            outline_index=outline_index,
            rationale=rationale,
        )
    )
    session.add(turn)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"interview_turn for attempt_id={attempt.id} violated a constraint: {exc}"
        ) from exc
    session.refresh(turn)
    return turn


def get_interview_turns(attempt_id: int, session: Session) -> list[InterviewTurn]:
    """Return ``interview_turns`` rows for ``attempt_id`` ordered by ``id`` ASC.

    An empty attempt returns ``[]`` — never raises on a missing attempt
    id because the SRA-805 router calls this on every turn before the
    first row is inserted and the SRA-803 graph resumes a closed tab
    against an attempt that may legitimately have no turns.
    """
    statement = (
        select(InterviewTurn)
        .where(InterviewTurn.attempt_id == attempt_id)  # pyright: ignore[reportArgumentType]
        .order_by(InterviewTurn.id)  # pyright: ignore[reportArgumentType]
    )
    return list(session.exec(statement).all())


def complete_interview_attempt(
    *,
    attempt: QuizAttempt,
    mean_score: float,
    session: Session,
) -> QuizAttempt:
    """Stamp ``attempt.completed_at`` and ``attempt.score`` (rounded mean, clamped [0,100]).

    ``mean_score`` is rounded to the nearest integer and clamped to
    ``[0, 100]`` so a stray ``None`` or out-of-range float from the
    caller cannot corrupt the attempt. Raises ``ValueError`` when
    ``attempt.completed_at`` is already set so the SRA-803 graph and
    the SRA-805 router cannot silently double-complete an attempt.
    """
    if attempt.completed_at is not None:
        raise ValueError(
            f"attempt_id {attempt.id} is already completed at {attempt.completed_at.isoformat()}"
        )

    score = round(mean_score)
    if score < 0:
        score = 0
    elif score > 100:
        score = 100

    attempt.score = score
    attempt.completed_at = _utcnow()

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
    ``quiz_sets`` (which clears ``quiz_questions``), ``quiz_attempts``
    (which clears ``quiz_answers`` and ``interview_turns``), and
    ``interview_plans``. A detached/already-deleted user raises
    ``UserNotFoundError`` so callers can distinguish a successful delete from
    a no-op. The caller owns the surrounding transaction / commit.
    """
    if user.id is None or session.get(User, user.id) is None:
        raise UserNotFoundError(f"user {getattr(user, 'id', None)!r} is not present in this session")

    user_id = user.id

    # Assessment sessions point at entries, quiz sets, plans, and attempts;
    # remove the parent orchestration records before their child artifacts.
    for assessment in session.exec(
        select(AssessmentSession).where(AssessmentSession.user_id == user_id)
    ).all():
        session.delete(assessment)
    session.flush()

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
        # interview_turns shares the same FK target and has the same
        # no-default-ondelete shape, so clear them alongside quiz_answers
        # to keep the cascade portable to engines without PRAGMA FKs.
        for turn in session.exec(
            select(InterviewTurn).where(
                InterviewTurn.attempt_id.in_(attempt_ids)  # type: ignore[arg-type]
            )
        ).all():
            session.delete(turn)

    # quiz_attempts must die first so the quiz_sets->quiz_attempts link is
    # free when we clear quiz_sets.
    for attempt in session.exec(
        select(QuizAttempt).where(QuizAttempt.user_id == user_id)
    ).all():
        session.delete(attempt)

    # quiz_sets and the dependent quiz_questions row by row (no FK cascade from
    # the platform default; SQLAlchemy ORM cascade may not be configured for
    # quiz_set -> questions). InterviewPlans reference the same quiz_set_id
    # FK with the same no-default-ondelete shape, so clear them alongside
    # quiz_questions to keep the cascade portable to engines without
    # PRAGMA FKs.
    for quiz_set in session.exec(
        select(QuizSet).where(QuizSet.user_id == user_id)
    ).all():
        for question in session.exec(
            select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
        ).all():
            session.delete(question)
        for plan in session.exec(
            select(InterviewPlan).where(
                InterviewPlan.quiz_set_id == quiz_set.id  # pyright: ignore[reportArgumentType]
            )
        ).all():
            session.delete(plan)
        session.delete(quiz_set)

    # study_entries and the dependent review_schedules + reference_materials.
    # The reference_materials.entry_id FK is declared with ondelete="CASCADE"
    # so the database engine itself can clear rows on the parent death; the
    # explicit loop is required for SQLite test runs where FK enforcement is
    # disabled, mirroring the quiz_attempts/interview_turns treatment above.
    for entry in session.exec(
        select(StudyEntry).where(StudyEntry.user_id == user_id)
    ).all():
        for review in session.exec(
            select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)
        ).all():
            session.delete(review)
        for ref in session.exec(
            select(ReferenceMaterial).where(ReferenceMaterial.entry_id == entry.id)
        ).all():
            session.delete(ref)
        session.delete(entry)

    session.delete(user)
    session.flush()


from datetime import timedelta  # noqa: E402  -- late import keeps the SRA-601 surface above the cascade
