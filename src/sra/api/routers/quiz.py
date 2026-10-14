from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from sra.agents.quiz_conversation_graph import (
    QuizAttemptAlreadyCompletedError,
    QuizVoiceInterpretationError,
    process_voice_turn,
)
from sra.api.deps import get_session, require_user
from sra.api.schemas import (
    AnswerResult,
    EntryOut,
    QuizAttemptOut,
    QuizQuestionOut,
    QuizSetOut,
    StartAttemptRequest,
    SubmitAnswerRequest,
    VoiceTurnRequest,
    VoiceTurnResponse,
)
from sra.db.models import (
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
        created_at=entry.created_at,
    )


def _question_to_out(question: QuizQuestion) -> QuizQuestionOut:
    assert question.id is not None
    return QuizQuestionOut(
        id=question.id,
        question_text=question.question_text,
        options=list(question.options),
        order_index=question.order_index,
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


@router.get("/today", response_model=list[QuizSetOut])
def list_today_quiz(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[QuizSetOut]:
    assert user.id is not None
    quiz_sets = list_today_quiz_sets(
        user_id=user.id, today=date.today(), session=session
    )
    return [_build_quiz_set_out(session, qs) for qs in quiz_sets]


@router.get("/{quiz_set_id}", response_model=QuizSetOut)
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
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    assert user.id is not None
    quiz_set = _get_owned_quiz_set(session, user.id, quiz_set_id)
    assert quiz_set.id is not None
    attempt = QuizAttempt.model_validate(
        QuizAttemptCreate(
            quiz_set_id=quiz_set.id,
            user_id=user.id,
            mode=body.mode,
            total_questions=quiz_set.question_count,
        )
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None
    if body.mode == "voice":
        questions = get_quiz_questions(quiz_set.id, session)
        first_question = questions[0]
        return {
            "attempt_id": attempt.id,
            "question_text": first_question.question_text,
            "options": list(first_question.options),
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