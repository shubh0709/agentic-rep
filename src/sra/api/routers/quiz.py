from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from sra.agents.interview_conversation_graph import (
    InterviewAttemptAlreadyCompletedError,
    InterviewAttemptFullError,
    InterviewLLMError,
    process_interview_turn,
)
from sra.agents.quiz_conversation_graph import (
    QuizAttemptAlreadyCompletedError,
    QuizVoiceInterpretationError,
    process_voice_turn,
)
from sra.api.deps import get_session, require_user
from sra.api.schemas import (
    AnswerResult,
    EntryOut,
    InterviewDetailOut,
    InterviewOutlineItemOut,
    InterviewPlanOut,
    InterviewSummaryOut,
    InterviewTurnOut,
    InterviewTurnRequest,
    InterviewTurnResponse,
    QuizAttemptOut,
    QuizQuestionOut,
    QuizSetOut,
    StartAttemptRequest,
    SubmitAnswerRequest,
    VoiceTurnRequest,
    VoiceTurnResponse,
)
from sra.db.models import (
    InterviewPlan,
    InterviewTurn,
    InterviewTurnRole,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptCreate,
    QuizQuestion,
    QuizSet,
    StudyEntry,
    User,
)
from sra.db.repository import (
    complete_quiz_attempt,
    get_interview_turns,
    get_or_create_interview_plan,
    get_quiz_questions,
    list_today_quiz_sets,
    save_quiz_answer,
)

router = APIRouter(prefix="/quiz", tags=["quiz"])

VOICE_UNSUPPORTED = "Voice mode is not yet supported. See Phase 5."


def _get_owned_quiz_set(session: Session, user_id: int, quiz_set_id: int) -> QuizSet:
    quiz_set = session.get(QuizSet, quiz_set_id)
    if quiz_set is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if quiz_set.user_id != user_id:
        raise HTTPException(status_code=403, detail="Quiz not accessible")
    return quiz_set


def _get_owned_attempt(session: Session, user_id: int, attempt_id: int) -> QuizAttempt:
    attempt = session.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found")
    if attempt.user_id != user_id:
        raise HTTPException(status_code=403, detail="Attempt not accessible")
    return attempt


def _entry_to_entry_out(entry: StudyEntry) -> EntryOut:
    assert entry.id is not None
    return EntryOut(
        id=entry.id,
        topic=entry.topic,
        subtopic=entry.subtopic,
        tags=entry.tags,
        summary=entry.summary,
        raw_transcript=entry.raw_transcript,
        created_at=entry.created_at,
    )


def _question_to_out(question: QuizQuestion) -> QuizQuestionOut:
    assert question.id is not None
    return QuizQuestionOut(
        id=question.id,
        question_text=question.question_text,
        options=list(question.options),
        order_index=question.order_index,
        bloom_level=getattr(question, "bloom_level", None),
        source_passage_quote=getattr(question, "source_passage_quote", None),
        distractor_misconception=getattr(question, "distractor_misconception", None),
    )


def _interview_turn_to_out(turn: InterviewTurn) -> InterviewTurnOut:
    assert turn.id is not None
    return InterviewTurnOut(
        id=turn.id,
        role=turn.role,  # type: ignore[arg-type]
        content=turn.content,
        score=turn.score,
        outline_index=turn.outline_index,
        rationale=turn.rationale,
        created_at=turn.created_at,
    )


def _attempt_to_out(attempt: QuizAttempt) -> QuizAttemptOut:
    assert attempt.id is not None
    return QuizAttemptOut(
        id=attempt.id,
        quiz_set_id=attempt.quiz_set_id,
        mode=attempt.mode,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        score=attempt.score,
        total_questions=attempt.total_questions,
        cursor_on_plan=attempt.cursor_on_plan,
    )


def _interview_plan_to_out(plan: InterviewPlan) -> InterviewPlanOut:
    assert plan.id is not None
    outline: list[InterviewOutlineItemOut] = []
    for item in (plan.outline or []):
        subtopic = (item.get("subtopic") or "").strip() or "general"
        focus_prompt = (item.get("focus_prompt") or "").strip() or "general recap"
        difficulty = item.get("difficulty", "medium")
        if difficulty not in ("easy", "medium", "hard"):
            difficulty = "medium"
        outline.append(
            InterviewOutlineItemOut(
                subtopic=subtopic,
                difficulty=difficulty,  # type: ignore[arg-type]
                focus_prompt=focus_prompt,
            )
        )
    return InterviewPlanOut(
        id=plan.id,
        quiz_set_id=plan.quiz_set_id,
        outline=outline,
        target_questions=plan.target_questions,
        created_at=plan.created_at,
    )


def _build_quiz_set_out(session: Session, quiz_set: QuizSet) -> QuizSetOut:
    assert quiz_set.id is not None
    questions = list(
        session.exec(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_set_id == quiz_set.id)
            .order_by(QuizQuestion.order_index)  # pyright: ignore[reportArgumentType]
        )
    )
    entry = session.get(StudyEntry, quiz_set.entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return QuizSetOut(
        id=quiz_set.id,
        entry=_entry_to_entry_out(entry),
        generated_on=quiz_set.generated_on,
        questions=[_question_to_out(q) for q in questions],
    )


@router.get("/today", response_model=list[QuizSetOut], response_model_exclude_none=True)
def list_today_quiz(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[QuizSetOut]:
    assert user.id is not None
    quiz_sets = list_today_quiz_sets(
        user_id=user.id, today=date.today(), session=session
    )
    return [_build_quiz_set_out(session, qs) for qs in quiz_sets]


@router.get("/{quiz_set_id}", response_model=QuizSetOut, response_model_exclude_none=True)
def get_quiz_set(
    quiz_set_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> QuizSetOut:
    assert user.id is not None
    quiz_set = _get_owned_quiz_set(session, user.id, quiz_set_id)
    return _build_quiz_set_out(session, quiz_set)


@router.post("/{quiz_set_id}/attempts", status_code=201)
def start_quiz_attempt(
    quiz_set_id: int,
    body: StartAttemptRequest,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    assert user.id is not None
    quiz_set = _get_owned_quiz_set(session, user.id, quiz_set_id)
    assert quiz_set.id is not None
    questions = get_quiz_questions(quiz_set.id, session)
    total_questions = (
        quiz_set.question_count if body.mode == "interview" else len(questions)
    )
    attempt = QuizAttempt.model_validate(
        QuizAttemptCreate(
            quiz_set_id=quiz_set.id,
            user_id=user.id,
            mode=body.mode,
            total_questions=total_questions,
        )
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None
    if body.mode == "voice":
        first_question = questions[0]
        return {
            "attempt_id": attempt.id,
            "question_text": first_question.question_text,
            "options": list(first_question.options),
        }
    if body.mode == "interview":
        get_or_create_interview_plan(quiz_set.id, session)
        result = process_interview_turn(
            session=session, attempt=attempt, action="start"
        )
        request.app.state.last_attempt_id_for_test = attempt.id
        return {
            "attempt_id": attempt.id,
            "question_text": result.question_text,
            "transcript_so_far": result.transcript_so_far,
        }
    return {"attempt_id": attempt.id, "question_text": None}


@router.post("/attempts/{attempt_id}/answers", response_model=AnswerResult)
def submit_quiz_answer(
    attempt_id: int,
    body: SubmitAnswerRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> AnswerResult:
    assert user.id is not None
    attempt = _get_owned_attempt(session, user.id, attempt_id)
    if attempt.mode != "web":
        raise HTTPException(status_code=400, detail=VOICE_UNSUPPORTED)
    if attempt.completed_at is not None:
        raise HTTPException(status_code=409, detail="Attempt already completed")
    question = session.exec(
        select(QuizQuestion).where(
            QuizQuestion.id == body.question_id,
            QuizQuestion.quiz_set_id == attempt.quiz_set_id,
        )
    ).first()
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    is_correct = body.selected_option_index == question.correct_option_index
    save_quiz_answer(
        attempt=attempt,
        question=question,
        selected_option_index=body.selected_option_index,
        is_correct=is_correct,
        session=session,
    )
    return AnswerResult(
        is_correct=is_correct,
        correct_option_index=question.correct_option_index,
        explanation=question.explanation,
    )


@router.post("/attempts/{attempt_id}/voice-turn", response_model=VoiceTurnResponse)
def voice_turn(
    attempt_id: int,
    body: VoiceTurnRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> VoiceTurnResponse:
    assert user.id is not None
    attempt = _get_owned_attempt(session, user.id, attempt_id)
    if attempt.mode != "voice":
        raise HTTPException(status_code=400, detail=VOICE_UNSUPPORTED)
    if attempt.completed_at is not None:
        raise HTTPException(status_code=409, detail="Attempt already completed")
    try:
        result = process_voice_turn(
            session=session, attempt=attempt, answer_text=body.answer_text
        )
    except QuizAttemptAlreadyCompletedError as exc:
        raise HTTPException(status_code=409, detail="Attempt already completed") from exc
    except QuizVoiceInterpretationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VoiceTurnResponse(
        is_correct=result["is_correct"],
        feedback_text=result["feedback_text"],
        next_question_text=result["next_question_text"],
        finished=result["finished"],
        score=result["score"],
    )


@router.post("/attempts/{attempt_id}/complete")
def complete_quiz_attempt_route(
    attempt_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    assert user.id is not None
    attempt = _get_owned_attempt(session, user.id, attempt_id)
    if attempt.mode != "web":
        raise HTTPException(status_code=400, detail=VOICE_UNSUPPORTED)
    if attempt.completed_at is not None:
        raise HTTPException(status_code=409, detail="Attempt already completed")
    answers = list(
        session.exec(select(QuizAnswer).where(QuizAnswer.attempt_id == attempt.id))
    )
    score = sum(1 for a in answers if a.is_correct)
    attempt = complete_quiz_attempt(attempt=attempt, score=score, session=session)
    assert attempt.score is not None
    return {"score": attempt.score, "total_questions": attempt.total_questions}


@router.get("/attempts/{attempt_id}", response_model=QuizAttemptOut)
def get_quiz_attempt(
    attempt_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> QuizAttemptOut:
    assert user.id is not None
    attempt = _get_owned_attempt(session, user.id, attempt_id)
    assert attempt.id is not None
    return QuizAttemptOut(
        id=attempt.id,
        quiz_set_id=attempt.quiz_set_id,
        mode=attempt.mode,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        score=attempt.score,
        total_questions=attempt.total_questions,
    )


# ---------------------------------------------------------------------------
# SRA-805 — Interactive interview mode routes.
# Added at the end of the file so the existing 404 tests for the older
# ``POST /quiz/{id}/attempts`` / ``POST /quiz/attempts/{id}/answers`` paths
# keep matching first. The new routes inherit the per-user rate-limit bucket
# via the router-level ``limit_authenticated`` dependency registered in
# ``sra.api.routers.__init__``.
# ---------------------------------------------------------------------------

INTERVIEW_MODE_REQUIRED = "This endpoint requires an interview mode attempt"


@router.post(
    "/attempts/{attempt_id}/interview-turn",
    response_model=InterviewTurnResponse,
)
def interview_turn(
    attempt_id: int,
    body: InterviewTurnRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InterviewTurnResponse:
    assert user.id is not None
    attempt = _get_owned_attempt(session, user.id, attempt_id)
    if attempt.mode != "interview":
        raise HTTPException(status_code=400, detail=INTERVIEW_MODE_REQUIRED)
    if attempt.completed_at is not None:
        raise HTTPException(status_code=409, detail="Attempt already completed")
    try:
        result = process_interview_turn(
            session=session,
            attempt=attempt,
            action=body.action,
            answer_text=body.answer_text,
        )
    except InterviewAttemptAlreadyCompletedError as exc:
        raise HTTPException(
            status_code=409, detail="Attempt already completed"
        ) from exc
    except InterviewAttemptFullError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InterviewLLMError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.refresh(attempt)
    assert attempt.id is not None
    turns = get_interview_turns(attempt.id, session)
    transcript = [_interview_turn_to_out(t) for t in turns]
    return InterviewTurnResponse(
        question_text=result.question_text,
        score=result.score,
        rationale=result.rationale,
        feedback=result.feedback,
        finished=result.finished,
        summary=result.summary,
        transcript_so_far=transcript,
        attempt=_attempt_to_out(attempt),
    )


@router.get(
    "/attempts/{attempt_id}/interview",
    response_model=InterviewDetailOut,
)
def get_interview(
    attempt_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InterviewDetailOut:
    assert user.id is not None
    attempt = _get_owned_attempt(session, user.id, attempt_id)
    quiz_set = session.get(QuizSet, attempt.quiz_set_id)
    if quiz_set is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    assert quiz_set.id is not None
    _, plan = get_or_create_interview_plan(quiz_set.id, session)
    assert attempt.id is not None
    turns = get_interview_turns(attempt.id, session)
    turn_outs = [_interview_turn_to_out(t) for t in turns]
    summary_out: InterviewSummaryOut | None = None
    if attempt.completed_at is not None:
        summary_turns = [
            t for t in turns if t.role == InterviewTurnRole.SYSTEM_SUMMARY.value
        ]
        summary_text = summary_turns[0].content if summary_turns else ""
        user_turns = [
            t for t in turns if t.role == InterviewTurnRole.USER.value
        ]
        user_scores = [t.score for t in user_turns if isinstance(t.score, int)]
        mean_score = sum(user_scores) // len(user_scores) if user_scores else 0
        total = len(plan.outline or [])
        answered = len(user_turns)
        remaining = max(total - answered, 0)
        summary_out = InterviewSummaryOut(
            score=mean_score,
            answered_count=answered,
            remaining_count=remaining,
            summary_text=summary_text,
        )
    return InterviewDetailOut(
        plan=_interview_plan_to_out(plan),
        turns=turn_outs,
        summary=summary_out,
        attempt=_attempt_to_out(attempt),
    )
