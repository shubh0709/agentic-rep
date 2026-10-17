"""SRA-901 / SRA-902 / SRA-903 / SRA-904 — ``POST /api/v1/topics`` and related routes.

This router lives behind the Phase 3 ``require_user`` + ``limit_authenticated``
dependencies (registered in :mod:`sra.api.routers`) so every request consumes
one token from the caller's bucket. The endpoints are:

  * ``POST /api/v1/topics`` -- create a ``StudyEntry`` and one or more
    ``reference_materials`` rows. URLs are fetched synchronously via
    :mod:`sra.sources.fetcher`; uploads are decoded and extracted via
    :mod:`sra.sources.doc_extractor`. SRA-903 / SRA-904 wire generation
    into the create flow too, so a successful create now carries a
    ``quiz_set_id`` and an ``interview_plan_id``. A reference whose URL
    fetch or doc extraction fails produces ``fetch_status="failed"`` and
    an entry in ``errors``; one failure does not abort the request.
  * ``POST /api/v1/topics/{entry_id}/regenerate`` -- re-run both
    pipelines against the existing entry + references (SRA-903/904).
    Returns 404 on a missing entry, 403 on a cross-user entry, 200 with
    ``TopicRegenerateResult`` (or partial-success with errors) on the
    happy path.
  * ``GET /api/v1/entries/{entry_id}/references`` -- list the references
    attached to the entry, scoped to the caller via ``StudyEntry.user_id``
    (cross-user reads get ``403``, missing entries get ``404``).
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from sra.agents import interview_planner, quiz_graph
from sra.agents.quiz_graph import QuizGenerationError
from sra.api.deps import get_session, require_user
from sra.api.schemas import (
    EntryOut,
    ReferenceCreate,
    ReferenceOut,
    TopicCreate,
    TopicCreateResult,
    TopicRegenerateResult,
)
from sra.db import repository
from sra.db.models import (
    InterviewPlan,
    QuizSet,
    ReferenceMaterial,
    StudyEntry,
    StudyEntryCreate,
    User,
)
from sra.jobs.quiz_pregeneration import _maybe_create_interview_plan
from sra.sources import doc_extractor, fetcher

router = APIRouter(tags=["topics"])
logger = logging.getLogger(__name__)


def _reference_row_to_out(row: ReferenceMaterial) -> ReferenceOut:
    """Render a persisted :class:`ReferenceMaterial` row as ``ReferenceOut``.

    For paste references the user-supplied ``pasted_text`` is mirrored into
    ``extracted_text`` so the dashboard renders a single uniform view; for
    url / upload refs the ``extracted_text`` is whatever the fetcher /
    extractor produced. ``fetched_at`` is intentionally NOT in the wire
    shape -- the dashboard does not need a second timestamp.
    """
    assert row.id is not None
    return ReferenceOut(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        url=row.url,
        pasted_text=row.pasted_text,
        file_name=row.file_name,
        content_type=row.content_type,
        extracted_text=row.extracted_text if row.extracted_text is not None else row.pasted_text,
        fetch_status=row.fetch_status,
        fetch_error=row.fetch_error,
        created_at=row.created_at,
    )


def _entry_to_entry_out(entry: StudyEntry) -> EntryOut:
    assert entry.id is not None
    return EntryOut(
        id=entry.id,
        topic=entry.topic,
        subtopic=entry.subtopic,
        tags=list(entry.tags),
        summary=entry.summary,
        raw_transcript=entry.raw_transcript,
        created_at=entry.created_at,
    )


def _decode_upload_bytes(file_b64: str) -> bytes:
    """Decode a base64-padded payload with strict error reporting.

    Raises ``ValueError`` (which the route maps to ``422`` via the existing
    Pydantic boundary) on malformed input so the caller can fix the request
    rather than discover the failure later in the extraction step.
    """
    try:
        return base64.b64decode(file_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"file_b64 is not valid base64: {exc}") from exc


def _ingest_reference(
    *,
    session: Session,
    entry_id: int,
    reference: ReferenceCreate,
) -> tuple[ReferenceMaterial, str | None]:
    """Persist one ``reference_materials`` row, fetching / extracting as needed.

    Returns ``(row, error_or_none)``: ``error_or_none`` is populated with a
    short human-readable message when the per-reference work failed; the
    route appends it to the partial-success ``errors`` list. The row is
    always persisted -- a ``failed`` fetch ends up as
    ``fetch_status="failed"`` with ``fetch_error`` so the dashboard can
    explain why.
    """

    if reference.kind == "paste":
        row = repository.add_reference(
            session=session,
            entry_id=entry_id,
            kind="paste",
            title=reference.title,
            pasted_text=reference.pasted_text,
            extracted_text=reference.pasted_text,
            fetch_status="fetched",
        )
        return row, None

    if reference.kind == "url":
        assert reference.url is not None
        result = fetcher.fetch_url_text(reference.url)
        if result.ok:
            row = repository.add_reference(
                session=session,
                entry_id=entry_id,
                kind="url",
                title=reference.title,
                url=reference.url,
                extracted_text=result.text,
                fetch_status="fetched",
            )
            return row, None
        row = repository.add_reference(
            session=session,
            entry_id=entry_id,
            kind="url",
            title=reference.title,
            url=reference.url,
            extracted_text=None,
            fetch_status="failed",
            fetch_error=result.error or "fetch_failed",
        )
        return row, f"could not fetch {reference.url}: {result.error or 'unknown'}"

    if reference.kind == "upload":
        assert reference.file_b64 is not None
        assert reference.file_name is not None
        assert reference.content_type is not None
        try:
            content = _decode_upload_bytes(reference.file_b64)
        except ValueError as exc:
            row = repository.add_reference(
                session=session,
                entry_id=entry_id,
                kind="upload",
                title=reference.title,
                file_name=reference.file_name,
                content_type=reference.content_type,
                extracted_text=None,
                fetch_status="failed",
                fetch_error=f"decode_failed: {exc}",
            )
            return row, f"could not decode upload: {exc}"
        extraction = doc_extractor.extract_document_text(
            content,
            content_type=reference.content_type,
            file_name=reference.file_name,
        )
        if extraction.ok:
            row = repository.add_reference(
                session=session,
                entry_id=entry_id,
                kind="upload",
                title=reference.title,
                file_name=reference.file_name,
                content_type=reference.content_type,
                extracted_text=extraction.text,
                fetch_status="fetched",
            )
            return row, None
        row = repository.add_reference(
            session=session,
            entry_id=entry_id,
            kind="upload",
            title=reference.title,
            file_name=reference.file_name,
            content_type=reference.content_type,
            extracted_text=None,
            fetch_status="failed",
            fetch_error=extraction.error or "extraction_failed",
        )
        return row, f"could not extract {reference.file_name}: {extraction.error or 'unknown'}"

    raise ValueError(f"unsupported reference kind: {reference.kind!r}")


@router.post("/topics", status_code=201, response_model=TopicCreateResult)
def create_topic(
    body: TopicCreate,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> TopicCreateResult:
    """Create a topic (entry + references) and return the persisted shape.

    Validates the body via the Pydantic boundary first (any kind-discriminator
    violation, empty ``references``, blank ``topic``, etc. -> 422 before any
    DB write). URL fetches and document extractions run synchronously inside
    the handler -- the longest realistic chain is one slow URL at 8 s.

    On success: returns the new entry, the references with ``extracted_text``
    populated where applicable, ``quiz_set_id=None``, ``interview_plan_id=None``
    (SRA-903 / SRA-904 wire those in), and an ``errors`` list empty on the
    happy path or populated per failed reference.
    """
    assert user.id is not None

    entry = repository.create_entry(
        entry_create=StudyEntryCreate(
            topic=body.topic,
            subtopic=body.subtopic,
            tags=list(body.tags),
            summary=body.summary or "",
            raw_transcript="",
        ),
        user_id=user.id,
        session=session,
    )
    assert entry.id is not None

    persisted: list[ReferenceMaterial] = []
    errors: list[str] = []
    for reference in body.references:
        row, error = _ingest_reference(
            session=session, entry_id=entry.id, reference=reference
        )
        persisted.append(row)
        if error is not None:
            errors.append(error)

    quiz_set_id: int | None = None
    interview_plan_id: int | None = None

    prefs = repository.get_user_preferences(user.id, session)
    question_count = prefs["quiz_question_count"]
    option_count = prefs["quiz_option_count"]
    interview_question_count = prefs["interview_question_count"]

    quiz_set: QuizSet | None = None
    try:
        quiz_set = quiz_graph.generate_and_persist_questions(
            entry=entry,
            user_id=user.id,
            session=session,
            question_count=question_count,
            option_count=option_count,
        )
    except QuizGenerationError as exc:
        errors.append(f"quiz: {exc}")
    except Exception:
        logger.exception(
            "Topic create: quiz generation failed for entry_id=%s", entry.id
        )
        errors.append("quiz: unexpected error during generation")

    if quiz_set is not None and quiz_set.id is not None:
        quiz_set_id = quiz_set.id
        try:
            outcome = _maybe_create_interview_plan(
                quiz_set=quiz_set,
                entry=entry,
                interview_question_count=interview_question_count,
                session=session,
            )
            if outcome in ("created", "skipped"):
                plan_row = session.exec(
                    select(InterviewPlan).where(
                        InterviewPlan.quiz_set_id == quiz_set.id  # pyright: ignore[reportArgumentType]
                    )
                ).first()
                if plan_row is not None:
                    interview_plan_id = plan_row.id
        except Exception:
            logger.exception(
                "Topic create: interview plan creation failed for entry_id=%s",
                entry.id,
            )
            errors.append("interview: unexpected error during planning")

    request.app.state.last_topic_id_for_test = entry.id
    return TopicCreateResult(
        entry=_entry_to_entry_out(entry),
        references=[_reference_row_to_out(row) for row in persisted],
        quiz_set_id=quiz_set_id,
        interview_plan_id=interview_plan_id,
        errors=errors,
    )


@router.post(
    "/topics/{entry_id}/regenerate",
    response_model=TopicRegenerateResult,
)
def regenerate_topic(
    entry_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> TopicRegenerateResult:
    """Re-run both generation pipelines against an existing entry + its references.

    Boundary checks run first: ``require_user`` (401), missing entry (404),
    cross-user entry (403). On the happy path the handler:

      1. resolves the caller's per-user quiz + interview preferences,
      2. pulls the entry's ``reference_materials`` bundle via
         :func:`repository.get_reference_text_bundle`,
      3. generates today's ``QuizSet`` when missing, or replaces the questions
         in today's existing set without changing its identity,
      4. creates or replaces the ``InterviewPlan`` linked to that quiz set,
      5. returns a :class:`TopicRegenerateResult` carrying the new IDs and
         any per-pipeline error messages.

    ``generation_errors`` is empty on the happy path; a non-empty list means
    one pipeline surfaced a typed error (``QuizGenerationError`` or
    ``RepositoryError``). The handler still returns ``200`` so the dashboard
    can render partial-success -- a failure is per-pipeline, not whole-call.
    """
    assert user.id is not None

    entry = session.get(StudyEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.user_id != user.id:
        raise HTTPException(status_code=403, detail="Entry not accessible")

    from sra.db.models import QuizSet

    prefs = repository.get_user_preferences(user.id, session)
    question_count = prefs["quiz_question_count"]
    option_count = prefs["quiz_option_count"]
    interview_question_count = prefs["interview_question_count"]

    quiz_set_id: int | None = None
    interview_plan_id: int | None = None
    generation_errors: list[str] = []

    quiz_set: QuizSet | None = None
    try:
        existing_quiz_set = session.exec(
            select(QuizSet).where(
                QuizSet.entry_id == entry_id,  # pyright: ignore[reportArgumentType]
                QuizSet.generated_on == datetime.now(UTC).date(),  # pyright: ignore[reportArgumentType]
            )
        ).first()
        if existing_quiz_set is None:
            quiz_set = quiz_graph.generate_and_persist_questions(
                entry=entry,
                user_id=user.id,
                session=session,
                question_count=question_count,
                option_count=option_count,
            )
        else:
            quiz_set = quiz_graph.regenerate_questions_in_place(
                quiz_set=existing_quiz_set,
                entry=entry,
                user_id=user.id,
                session=session,
                question_count=question_count,
                option_count=option_count,
            )
    except QuizGenerationError as exc:
        generation_errors.append(f"quiz: {exc}")
    except Exception:
        logger.exception(
            "Topic regenerate: quiz generation failed for entry_id=%s", entry_id
        )
        generation_errors.append("quiz: unexpected error during generation")

    if quiz_set is not None and quiz_set.id is not None:
        quiz_set_id = quiz_set.id
        try:
            references = repository.get_reference_text_bundle(
                entry_id, session=session
            )
            outline = interview_planner.generate_interview_outline(
                entry,
                interview_question_count,
                references=references,
            )
            plan_row = repository.replace_interview_plan(
                quiz_set=quiz_set,
                outline=outline,
                target_questions=interview_question_count,
                session=session,
            )
            if plan_row is not None:
                interview_plan_id = plan_row.id
        except Exception:
            logger.exception(
                "Topic regenerate: interview plan creation failed for entry_id=%s",
                entry_id,
            )
            generation_errors.append("interview: unexpected error during planning")

    return TopicRegenerateResult(
        quiz_set_id=quiz_set_id,
        interview_plan_id=interview_plan_id,
        generation_errors=generation_errors,
    )


@router.get(
    "/entries/{entry_id}/references",
    response_model=list[ReferenceOut],
)
def list_entry_references(
    entry_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ReferenceOut]:
    """Return this entry's references, scoped to the caller.

    404 on a missing entry, 403 on a cross-user read, 200 with an empty list
    on a valid entry that has no references.
    """
    assert user.id is not None
    entry = session.get(StudyEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.user_id != user.id:
        raise HTTPException(status_code=403, detail="Entry not accessible")
    rows = list(
        session.exec(
            select(ReferenceMaterial).where(
                ReferenceMaterial.entry_id == entry_id  # pyright: ignore[reportArgumentType]
            )
        ).all()
    )
    return [_reference_row_to_out(row) for row in rows]


__all__ = ["router"]
