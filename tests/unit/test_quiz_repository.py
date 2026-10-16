
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-402 quiz repository helper contracts."""

from datetime import date

import pytest
from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.db import repository
from sra.db.models import (
    QuizAttempt,
    QuizQuestion,
    StudyEntry,
)

pytestmark = pytest.mark.phase4


def _make_entry(session, user, topic: str = "Photosynthesis") -> StudyEntry:
    entry = StudyEntry(
        user_id=user.id,
        topic=topic,
        summary=f"{topic} summary",
        raw_transcript=f"today I studied {topic}",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    return entry


def _drafts(n: int, option_count: int = 4) -> list[MCQDraft]:
    return [
        MCQDraft(
            question_text=f"q{i}",
            options=[f"opt{j}" for j in range(option_count)],
            correct_option_index=i % option_count,
            explanation=f"why {i}",
        )
        for i in range(n)
    ]


def test_create_quiz_set_persists_set_and_questions_in_order(session, seeded_user):
    entry = _make_entry(session, seeded_user)

    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=3,
        option_count=4,
        drafts=_drafts(3, option_count=4),
    )

    assert quiz_set.id is not None
    assert quiz_set.entry_id == entry.id
    assert quiz_set.user_id == seeded_user.id
    assert quiz_set.question_count == 3
    assert quiz_set.option_count == 4
    assert quiz_set.generated_on == date.today()

    rows = session.exec(select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)).all()
    assert len(rows) == 3
    for index, row in enumerate(rows):
        assert row.order_index == index
        assert len(row.options) == 4
        assert 0 <= row.correct_option_index < 4


def test_create_quiz_set_is_idempotent_same_day(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    today = date.today()

    repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=today,
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )

    with pytest.raises(ValueError):
        repository.create_quiz_set(
            entry=entry,
            user_id=seeded_user.id,
            session=session,
            generated_on=today,
            question_count=1,
            option_count=4,
            drafts=_drafts(1, option_count=4),
        )


def test_get_quiz_set_returns_none_for_another_user(session, seeded_user, other_user):
    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    assert quiz_set.id is not None

    result = repository.get_quiz_set(other_user.id, quiz_set.id, session)

    assert result is None


def test_get_quiz_set_returns_set_for_owner(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    assert quiz_set.id is not None

    result = repository.get_quiz_set(seeded_user.id, quiz_set.id, session)

    assert result is not None
    assert result.id == quiz_set.id


def test_list_today_quiz_sets_filters_by_user_and_date(session, seeded_user, other_user):
    mine_today = _make_entry(session, seeded_user, topic="MineToday")
    mine_yesterday_entry = _make_entry(session, seeded_user, topic="MineYesterday")
    theirs = _make_entry(session, other_user, topic="TheirsToday")

    today = date.today()
    yesterday = today.replace(day=today.day) if today.day != 1 else today
    yesterday = date.fromordinal(today.toordinal() - 1)

    repository.create_quiz_set(
        entry=mine_today,
        user_id=seeded_user.id,
        session=session,
        generated_on=today,
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    repository.create_quiz_set(
        entry=mine_yesterday_entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=yesterday,
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    repository.create_quiz_set(
        entry=theirs,
        user_id=other_user.id,
        session=session,
        generated_on=today,
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )

    today_sets = repository.list_today_quiz_sets(seeded_user.id, today, session)

    assert len(today_sets) == 1
    assert today_sets[0].user_id == seeded_user.id
    assert today_sets[0].generated_on == today


def test_save_quiz_answer_enforces_uniqueness(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    assert quiz_set.id is not None
    question = session.exec(select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)).one()
    attempt = QuizAttempt(quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1)
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None

    repository.save_quiz_answer(
        attempt=attempt,
        question=question,
        selected_option_index=0,
        is_correct=True,
        session=session,
    )

    with pytest.raises(ValueError):
        repository.save_quiz_answer(
            attempt=attempt,
            question=question,
            selected_option_index=1,
            is_correct=False,
            session=session,
        )


def test_save_quiz_answer_records_selected_index_and_correctness(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    assert quiz_set.id is not None
    question = session.exec(select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)).one()
    attempt = QuizAttempt(quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1)
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None

    answer = repository.save_quiz_answer(
        attempt=attempt,
        question=question,
        selected_option_index=2,
        is_correct=False,
        session=session,
    )

    assert answer.attempt_id == attempt.id
    assert answer.question_id == question.id
    assert answer.selected_option_index == 2
    assert answer.is_correct is False
    assert answer.answered_at is not None
    assert answer.answered_at.tzinfo is not None


def test_complete_quiz_attempt_stamps_completed_at_and_score(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=2,
        option_count=4,
        drafts=_drafts(2, option_count=4),
    )
    assert quiz_set.id is not None
    attempt = QuizAttempt(quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=2)
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None

    completed = repository.complete_quiz_attempt(attempt=attempt, score=1, session=session)

    assert completed.id == attempt.id
    assert completed.score == 1
    assert completed.completed_at is not None
    assert completed.completed_at.tzinfo is not None


def test_get_quiz_attempt_enforces_ownership(session, seeded_user, other_user):
    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    assert quiz_set.id is not None
    attempt = QuizAttempt(quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1)
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None

    own = repository.get_quiz_attempt(seeded_user.id, attempt.id, session)
    other_result = repository.get_quiz_attempt(other_user.id, attempt.id, session)

    assert own is not None
    assert own.id == attempt.id
    assert other_result is None


# ---------------------------------------------------------------------------
# SRA-804 — Interview repository helpers
# ---------------------------------------------------------------------------


phase8 = pytest.mark.phase8  # noqa: E402  -- marker alias below the Phase 4 marker


@phase8
def test_create_interview_plan_is_idempotent_on_same_quiz_set_id(session, seeded_user):
    from sra.db.models import InterviewPlan
    from sra.db.repository import create_interview_plan

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    assert quiz_set.id is not None
    outline = [
        {"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"},
        {"subtopic": "b", "difficulty": "medium", "focus_prompt": "y"},
    ]
    first = create_interview_plan(
        quiz_set=quiz_set,
        outline=outline,
        target_questions=2,
        session=session,
    )
    second = create_interview_plan(
        quiz_set=quiz_set,
        outline=outline,
        target_questions=2,
        session=session,
    )
    assert first.id == second.id
    assert session.exec(select(InterviewPlan)).one().id == first.id


@phase8
def test_create_interview_plan_returns_none_when_outline_is_empty(session, seeded_user):
    from sra.db.repository import create_interview_plan

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    result = create_interview_plan(
        quiz_set=quiz_set,
        outline=[],
        target_questions=0,
        session=session,
    )
    assert result is None


@phase8
def test_get_or_create_interview_plan_raises_value_error_when_quiz_set_missing(session):
    from sra.db.repository import get_or_create_interview_plan

    with pytest.raises(ValueError):
        get_or_create_interview_plan(999_999, session)


@phase8
def test_get_or_create_interview_plan_returns_existing_plan(session, seeded_user):
    from sra.db.repository import create_interview_plan, get_or_create_interview_plan

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    outline = [{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}]
    plan = create_interview_plan(
        quiz_set=quiz_set, outline=outline, target_questions=1, session=session
    )
    assert plan.id is not None

    _quiz_set, returned = get_or_create_interview_plan(quiz_set.id, session)
    assert returned.id == plan.id


@phase8
def test_get_or_create_interview_plan_creates_placeholder_when_missing(session, seeded_user):
    from sra.db.repository import get_or_create_interview_plan

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    assert quiz_set.id is not None
    _quiz_set, plan = get_or_create_interview_plan(quiz_set.id, session)
    assert plan.id is not None
    assert plan.quiz_set_id == quiz_set.id
    assert plan.outline  # placeholder outline is non-empty


@phase8
def test_append_interview_turn_rejects_unknown_role(session, seeded_user):
    from sra.db.repository import append_interview_turn

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    with pytest.raises(ValueError):
        append_interview_turn(
            attempt=attempt,
            role="bogus",
            content="x",
            session=session,
        )


@phase8
def test_append_interview_turn_rejects_empty_content(session, seeded_user):
    from sra.db.repository import append_interview_turn

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    with pytest.raises(ValueError):
        append_interview_turn(
            attempt=attempt,
            role="ai",
            content="   ",
            session=session,
        )


@phase8
def test_get_interview_turns_returns_rows_in_id_asc(session, seeded_user):
    from sra.db.repository import (
        append_interview_turn,
        create_interview_plan,
        get_interview_turns,
    )

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert quiz_set.id is not None
    outline = [{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}]
    create_interview_plan(quiz_set=quiz_set, outline=outline, target_questions=1, session=session)

    append_interview_turn(attempt=attempt, role="ai", content="q1", session=session)
    append_interview_turn(attempt=attempt, role="user", content="answer1", score=70, session=session)
    append_interview_turn(attempt=attempt, role="ai", content="q2", session=session)

    turns = get_interview_turns(attempt.id, session)
    assert [t.content for t in turns] == ["q1", "answer1", "q2"]
    assert [t.role for t in turns] == ["ai", "user", "ai"]


@phase8
def test_get_interview_turns_returns_empty_when_no_rows(session, seeded_user):
    from sra.db.repository import get_interview_turns

    assert get_interview_turns(999_999, session) == []


@phase8
def test_complete_interview_attempt_sets_rounded_mean_and_completed_at(session, seeded_user):
    from sra.db.repository import (
        append_interview_turn,
        complete_interview_attempt,
        create_interview_plan,
    )

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    outline = [{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}]
    create_interview_plan(
        quiz_set=quiz_set, outline=outline, target_questions=1, session=session
    )
    append_interview_turn(attempt=attempt, role="user", content="a", score=70, session=session)
    append_interview_turn(attempt=attempt, role="user", content="b", score=80, session=session)

    mean = (70 + 80) / 2
    completed = complete_interview_attempt(attempt=attempt, mean_score=mean, session=session)

    assert completed.score == 75
    assert completed.completed_at is not None
    assert completed.completed_at.tzinfo is not None


@phase8
def test_complete_interview_attempt_called_twice_raises(session, seeded_user):
    from sra.db.repository import (
        append_interview_turn,
        complete_interview_attempt,
        create_interview_plan,
    )

    entry = _make_entry(session, seeded_user)
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=_drafts(1, option_count=4),
    )
    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    outline = [{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}]
    create_interview_plan(
        quiz_set=quiz_set, outline=outline, target_questions=1, session=session
    )
    append_interview_turn(attempt=attempt, role="user", content="a", score=50, session=session)
    complete_interview_attempt(attempt=attempt, mean_score=50, session=session)

    with pytest.raises(ValueError):
        complete_interview_attempt(attempt=attempt, mean_score=50, session=session)