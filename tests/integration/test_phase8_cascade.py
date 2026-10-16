
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-804 — ``delete_user_cascade`` removes all ``interview_plans`` and ``interview_turns`` rows."""

from datetime import date

import pytest
from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.db import repository
from sra.db.models import (
    QuizAttempt,
    QuizSet,
    StudyEntry,
    StudyEntryCreate,
    User,
    UserCreate,
)

# ``InterviewPlan`` and ``InterviewTurn`` are SRA-801 model symbols; import
# them lazily inside the test function so a missing symbol fails that test,
# not the whole module.

pytestmark = pytest.mark.phase8


def _make_user(session) -> User:
    user = User.model_validate(
        UserCreate(email="cascade@example.com", display_name="Cascade", google_sub="cascade-sub"),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    return user


def _make_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="s", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _drafts(n: int = 1) -> list[MCQDraft]:
    return [
        MCQDraft(
            question_text=f"q{i}",
            options=["a", "b"],
            correct_option_index=0,
            explanation="",
        )
        for i in range(n)
    ]


def test_delete_user_cascade_removes_interview_plans_and_turns(session):
    from sra.db.models import InterviewPlan, InterviewTurn

    user = _make_user(session)
    entry = _make_entry(session, user.id if user.id is not None else 0)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=2,
        drafts=_drafts(1),
    )
    assert quiz_set.id is not None

    interview_plan = repository.create_interview_plan(
        quiz_set=quiz_set,
        outline=[{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}],
        target_questions=1,
        session=session,
    )
    assert interview_plan is not None and interview_plan.id is not None

    attempt = QuizAttempt(
        user_id=user.id,
        quiz_set_id=quiz_set.id,
        mode="interview",
        total_questions=1,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    repository.append_interview_turn(attempt=attempt, role="ai", content="q1", session=session)
    repository.append_interview_turn(attempt=attempt, role="user", content="a1", score=70, session=session)

    # Sanity: rows exist
    assert session.exec(select(InterviewPlan)).all() != []
    turns_before = session.exec(
        select(InterviewTurn).where(InterviewTurn.attempt_id == attempt.id)
    ).all()
    assert len(turns_before) >= 2

    repository.delete_user_cascade(user, session)
    session.commit()

    # All interview rows should be gone
    assert session.exec(select(InterviewPlan)).all() == []
    turns_after = session.exec(
        select(InterviewTurn).where(InterviewTurn.attempt_id == attempt.id)
    ).all()
    assert turns_after == []
    # QuizSet should also be gone (cascade chain)
    assert session.get(QuizSet, quiz_set.id) is None
