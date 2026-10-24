"""SRA-1101 — on-demand Interview Me assessment orchestration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from sra.agents import assessment_analyzer, assessment_reporter, interview_planner, quiz_graph
from sra.agents.assessment_reporter import AssessmentResultItem
from sra.agents.interview_conversation_graph import process_interview_turn
from sra.api.deps import get_session, require_user
from sra.api.routers.topics import _ingest_reference
from sra.api.schemas import AssessmentAnalyzeRequest, AssessmentGenerateRequest
from sra.db import repository
from sra.db.models import (
    AssessmentSession,
    AssessmentSessionCreate,
    InterviewPlan,
    InterviewTurnRole,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptCreate,
    QuizQuestion,
    StudyEntry,
    StudyEntryCreate,
    User,
    _utcnow,
)

router = APIRouter(prefix="/assessments", tags=["assessments"])


def _owned(assessment_id: int, user_id: int, session: Session) -> AssessmentSession:
    row = session.get(AssessmentSession, assessment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if row.user_id != user_id:
        raise HTTPException(status_code=403, detail="Assessment not accessible")
    return row


def _session_out(row: AssessmentSession, session: Session | None = None) -> dict[str, Any]:
    assert row.id is not None
    payload = {
        "id": row.id,
        "entry_id": row.entry_id,
        "topic": row.topic,
        "status": row.status,
        "suggestions": list(row.suggestions or []),
        "selected_topic_keys": list(row.selected_topic_keys or []),
        "mode": row.mode,
        "mcq_question_count": row.mcq_question_count,
        "interview_question_count": row.interview_question_count,
        "quiz_set_id": row.quiz_set_id,
        "interview_plan_id": row.interview_plan_id,
        "mcq_attempt_id": row.mcq_attempt_id,
        "interview_attempt_id": row.interview_attempt_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if session is not None:
        mcq_attempt = session.get(QuizAttempt, row.mcq_attempt_id) if row.mcq_attempt_id else None
        interview_attempt = (
            session.get(QuizAttempt, row.interview_attempt_id)
            if row.interview_attempt_id
            else None
        )
        payload["mcq_completed"] = bool(mcq_attempt and mcq_attempt.completed_at)
        payload["interview_completed"] = bool(
            interview_attempt and interview_attempt.completed_at
        )
        payload["mcq_answered_count"] = (
            len(
                session.exec(
                    select(QuizAnswer).where(
                        QuizAnswer.attempt_id == row.mcq_attempt_id
                    )
                ).all()
            )
            if row.mcq_attempt_id
            else 0
        )
    return payload


@router.post("/analyze", status_code=201)
def analyze_assessment(
    body: AssessmentAnalyzeRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    assert user.id is not None
    entry = repository.create_entry(
        entry_create=StudyEntryCreate(
            topic=body.topic,
            subtopic=body.subtopic,
            tags=list(body.tags),
            summary=body.summary or body.topic,
            raw_transcript="",
        ),
        user_id=user.id,
        session=session,
    )
    assert entry.id is not None
    errors: list[str] = []
    for reference in body.references:
        _row, error = _ingest_reference(session=session, entry_id=entry.id, reference=reference)
        if error:
            errors.append(error)
    reference_texts = repository.get_reference_text_bundle(entry.id, session=session)
    if not reference_texts:
        raise HTTPException(
            status_code=422,
            detail={"message": "No reference could be extracted", "errors": errors},
        )
    try:
        suggestions = assessment_analyzer.analyze_topics(
            topic=body.topic, reference_texts=reference_texts
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not analyze references: {exc}") from exc
    assessment = AssessmentSession.model_validate(
        AssessmentSessionCreate(topic=body.topic, suggestions=[item.model_dump() for item in suggestions.items]),
        update={"user_id": user.id, "entry_id": entry.id},
    )
    session.add(assessment)
    session.commit()
    session.refresh(assessment)
    return {**_session_out(assessment, session), "errors": errors}


@router.post("/{assessment_id}/generate")
def generate_assessment(
    assessment_id: int,
    body: AssessmentGenerateRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    assert user.id is not None
    assessment = _owned(assessment_id, user.id, session)
    if assessment.status != "draft":
        return _session_out(assessment, session)
    by_key = {item.get("key"): item for item in assessment.suggestions or []}
    unknown = [key for key in body.selected_topic_keys if key not in by_key]
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_topic_keys": unknown})
    focus_topics = [str(by_key[key]["label"]) for key in body.selected_topic_keys]
    entry = session.get(StudyEntry, assessment.entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Assessment entry not found")

    try:
        if body.mode in ("mcq", "both"):
            quiz_set = quiz_graph.generate_and_persist_questions(
                entry=entry,
                user_id=user.id,
                session=session,
                question_count=body.mcq_question_count,
                option_count=4,
                focus_topics=focus_topics,
            )
        else:
            quiz_set = repository.create_quiz_set(
                entry=entry,
                user_id=user.id,
                session=session,
                generated_on=date.today(),
                question_count=0,
                option_count=4,
                drafts=[],
            )
        assert quiz_set.id is not None
        plan: InterviewPlan | None = None
        if body.mode in ("interview", "both"):
            assert entry.id is not None
            outline = interview_planner.generate_interview_outline(
                entry,
                body.interview_question_count,
                references=repository.get_reference_text_bundle(entry.id, session=session),
                focus_topics=focus_topics,
            )
            plan = repository.create_interview_plan(
                quiz_set=quiz_set,
                outline=outline,
                target_questions=body.interview_question_count,
                session=session,
            )
            if plan is None:
                raise ValueError("interview planner returned an empty outline")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Assessment generation failed: {exc}") from exc

    assessment.selected_topic_keys = list(body.selected_topic_keys)
    assessment.mode = body.mode
    assessment.mcq_question_count = body.mcq_question_count
    assessment.interview_question_count = body.interview_question_count
    assessment.quiz_set_id = quiz_set.id
    assessment.interview_plan_id = plan.id if plan is not None else None
    assessment.status = "ready"
    assessment.updated_at = _utcnow()
    session.add(assessment)
    session.commit()
    session.refresh(assessment)
    return _session_out(assessment, session)


@router.post("/{assessment_id}/start/{mode}")
def start_assessment_mode(
    assessment_id: int,
    mode: Literal["mcq", "interview"],
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    assert user.id is not None
    assessment = _owned(assessment_id, user.id, session)
    if assessment.status not in ("ready", "in_progress") or assessment.quiz_set_id is None:
        raise HTTPException(status_code=409, detail="Assessment is not ready")
    if assessment.mode != "both" and assessment.mode != mode:
        raise HTTPException(status_code=409, detail=f"{mode} is not configured")
    field = "mcq_attempt_id" if mode == "mcq" else "interview_attempt_id"
    existing_id = getattr(assessment, field)
    if existing_id is not None:
        existing = session.get(QuizAttempt, existing_id)
        if existing is not None:
            return {"attempt_id": existing.id, "resumed": True}
    if mode == "interview" and assessment.mode == "both" and assessment.mcq_attempt_id is not None:
        mcq_attempt = session.get(QuizAttempt, assessment.mcq_attempt_id)
        if mcq_attempt is None or mcq_attempt.completed_at is None:
            raise HTTPException(status_code=409, detail="Complete the MCQ section first")
    total = assessment.mcq_question_count if mode == "mcq" else assessment.interview_question_count
    attempt = QuizAttempt.model_validate(
        QuizAttemptCreate(
            quiz_set_id=assessment.quiz_set_id,
            user_id=user.id,
            mode="web" if mode == "mcq" else "interview",
            total_questions=total,
        )
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None
    setattr(assessment, field, attempt.id)
    assessment.status = "in_progress"
    assessment.updated_at = _utcnow()
    session.add(assessment)
    session.commit()
    if mode == "interview":
        result = process_interview_turn(session=session, attempt=attempt, action="start")
        return {
            "attempt_id": attempt.id,
            "resumed": False,
            "question_text": result.question_text,
            "transcript_so_far": result.transcript_so_far,
        }
    return {"attempt_id": attempt.id, "resumed": False}


def _selected_labels(row: AssessmentSession) -> list[str]:
    wanted = set(row.selected_topic_keys or [])
    labels = [
        str(item.get("label", "General"))
        for item in row.suggestions or []
        if item.get("key") in wanted
    ]
    return labels or ["General"]


def _result_items(row: AssessmentSession, session: Session) -> list[AssessmentResultItem]:
    labels = _selected_labels(row)
    items: list[AssessmentResultItem] = []
    if row.mcq_attempt_id is not None:
        answers = list(
            session.exec(
                select(QuizAnswer)
                .where(QuizAnswer.attempt_id == row.mcq_attempt_id)
                .order_by(QuizAnswer.id)  # pyright: ignore[reportArgumentType]
            ).all()
        )
        for index, answer in enumerate(answers):
            question = session.get(QuizQuestion, answer.question_id)
            if question is None:
                continue
            selected = answer.selected_option_index
            answer_text = (
                question.options[selected]
                if selected is not None and 0 <= selected < len(question.options)
                else "No answer"
            )
            expected = question.options[question.correct_option_index]
            items.append(
                AssessmentResultItem(
                    kind="mcq",
                    topic=labels[index % len(labels)],
                    question=question.question_text,
                    answer=answer_text,
                    expected_answer=expected,
                    score=100 if answer.is_correct else 0,
                    is_correct=answer.is_correct,
                    feedback=question.explanation
                    or ("Correct." if answer.is_correct else "Review the correct option."),
                    rationale=(
                        "The selected option matches the grounded answer."
                        if answer.is_correct
                        else "The selected option does not match the grounded answer."
                    ),
                    evidence_excerpt=question.source_passage_quote or "",
                )
            )
    if row.interview_attempt_id is not None:
        turns = repository.get_interview_turns(row.interview_attempt_id, session)
        plan = session.get(InterviewPlan, row.interview_plan_id) if row.interview_plan_id else None
        for index, turn in enumerate(turns):
            if turn.role != InterviewTurnRole.USER.value:
                continue
            before = next((item for item in reversed(turns[:index]) if item.role == InterviewTurnRole.AI.value), None)
            after = next(
                (
                    item
                    for item in turns[index + 1 :]
                    if item.role == InterviewTurnRole.AI.value and item.score is not None
                ),
                None,
            )
            outline_index = turn.outline_index or 0
            plan_item = (plan.outline[outline_index] if plan and outline_index < len(plan.outline or []) else {})
            score = int(turn.score or 0)
            items.append(
                AssessmentResultItem(
                    kind="interview",
                    topic=str(plan_item.get("subtopic") or labels[outline_index % len(labels)]),
                    question=before.content if before else str(plan_item.get("focus_prompt", "Interview question")),
                    answer=turn.content,
                    expected_answer=str(plan_item.get("ideal_answer_rubric", "See the source-grounded rubric.")),
                    score=score,
                    is_correct=score >= 70,
                    feedback=after.content if after else "No feedback recorded.",
                    rationale=(
                        after.rationale
                        if after and after.rationale
                        else "Score assigned by the interview grader."
                    ),
                    evidence_excerpt=str(plan_item.get("source_passage_quote", "")),
                )
            )
    return items


@router.get("")
def list_assessments(
    user: User = Depends(require_user), session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    assert user.id is not None
    rows = session.exec(
        select(AssessmentSession)
        .where(AssessmentSession.user_id == user.id)
        .order_by(AssessmentSession.id.desc())  # pyright: ignore[reportAttributeAccessIssue,reportOptionalMemberAccess]
    ).all()
    return [_session_out(row, session) for row in rows]


@router.get("/{assessment_id}/results")
def assessment_results(
    assessment_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    assert user.id is not None
    row = _owned(assessment_id, user.id, session)
    if row.report is not None:
        return dict(row.report)
    required_ids = [row.mcq_attempt_id] if row.mode == "mcq" else [row.interview_attempt_id]
    if row.mode == "both":
        required_ids = [row.mcq_attempt_id, row.interview_attempt_id]
    attempts = [session.get(QuizAttempt, attempt_id) if attempt_id else None for attempt_id in required_ids]
    if not attempts or any(attempt is None or attempt.completed_at is None for attempt in attempts):
        raise HTTPException(status_code=409, detail="Assessment is not complete")
    report = assessment_reporter.build_assessment_report(
        mode=row.mode,  # type: ignore[arg-type]
        items=_result_items(row, session),
    ).model_dump()
    payload = {"status": "completed", **report}
    row.report = payload
    row.status = "completed"
    row.completed_at = datetime.now(UTC)
    row.updated_at = _utcnow()
    session.add(row)
    session.commit()
    return payload


@router.get("/{assessment_id}")
def get_assessment(
    assessment_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    assert user.id is not None
    return _session_out(_owned(assessment_id, user.id, session), session)


__all__ = ["router"]
