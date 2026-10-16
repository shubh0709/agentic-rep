"""SRA-401 quiz-schema contracts, written before the migration lands."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from sra.db.models import (
    QuizAnswer,
    QuizAnswerCreate,
    QuizAttempt,
    QuizAttemptCreate,
    QuizQuestion,
    QuizQuestionCreate,
    QuizSet,
    QuizSetCreate,
    StudyEntry,
    User,
    UserCreate,
)

pytestmark = pytest.mark.phase4


def _seed_entry(session: Session, user: User) -> StudyEntry:
    assert user.id is not None
    entry = StudyEntry(
        user_id=user.id,
        topic="Photosynthesis",
        summary="light + dark reactions",
        raw_transcript="today I studied photosynthesis",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    return entry


def test_quiz_set_defaults_snapshot_counts_and_today():
    qs = QuizSet.model_validate(
        QuizSetCreate(entry_id=1),
        update={"user_id": 1, "question_count": 3, "option_count": 4},
    )

    assert qs.entry_id == 1
    assert qs.user_id == 1
    assert qs.question_count == 3
    assert qs.option_count == 4
    assert qs.generated_on is not None


def test_quiz_question_defaults_options_to_empty_list():
    qq = QuizQuestion.model_validate(
        QuizQuestionCreate(
            quiz_set_id=1,
            question_text="What drives the light reactions?",
            correct_option_index=0,
            order_index=0,
        )
    )

    assert qq.options == []


def test_quiz_attempt_started_at_defaults_to_timezone_aware_utcnow():
    attempt = QuizAttempt.model_validate(
        QuizAttemptCreate(quiz_set_id=1, user_id=1, total_questions=3),
    )

    assert attempt.started_at is not None
    assert attempt.started_at.tzinfo is not None
    assert attempt.started_at.utcoffset() == datetime.now(UTC).utcoffset()


def test_quiz_answer_answered_at_defaults_to_timezone_aware_utcnow():
    answer = QuizAnswer.model_validate(
        QuizAnswerCreate(
            attempt_id=1,
            question_id=1,
            is_correct=True,
        )
    )

    assert answer.answered_at is not None
    assert answer.answered_at.tzinfo is not None


def test_quiz_set_create_rejects_server_owned_fields():
    with pytest.raises(ValidationError):
        QuizSetCreate(entry_id=1, user_id=1, generated_on=datetime.now(UTC).date())  # pyright: ignore[reportCallIssue]


def test_quiz_question_create_rejects_server_owned_fields():
    with pytest.raises(ValidationError):
        QuizQuestionCreate(quiz_set_id=1, question_text="x", correct_option_index=0, order_index=0, id=99)  # pyright: ignore[reportCallIssue]


def test_quiz_attempt_create_rejects_server_owned_fields():
    with pytest.raises(ValidationError):
        QuizAttemptCreate(
            quiz_set_id=1,
            user_id=1,
            total_questions=3,
            started_at=datetime.now(UTC),  # pyright: ignore[reportCallIssue]
        )


def test_quiz_answer_create_rejects_server_owned_fields():
    with pytest.raises(ValidationError):
        QuizAnswerCreate(
            attempt_id=1,
            question_id=1,
            is_correct=True,
            answered_at=datetime.now(UTC),  # pyright: ignore[reportCallIssue]
        )


def test_quiz_question_options_json_round_trips_through_sqlite(session, seeded_user):
    entry = _seed_entry(session, seeded_user)
    assert entry.id is not None
    today = datetime.now(UTC).date()
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={"user_id": seeded_user.id, "question_count": 1, "option_count": 3, "generated_on": today},
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    assert quiz_set.id is not None

    question = QuizQuestion.model_validate(
        QuizQuestionCreate(
            quiz_set_id=quiz_set.id,
            question_text="Which wavelength drives photosystem II?",
            options=["680 nm", "700 nm", "500 nm"],
            correct_option_index=0,
            order_index=0,
        )
    )
    session.add(question)
    session.commit()
    session.refresh(question)
    assert question.id is not None

    reloaded = session.exec(select(QuizQuestion).where(QuizQuestion.id == question.id)).one()
    assert reloaded.options == ["680 nm", "700 nm", "500 nm"]


def test_quiz_set_unique_constraint_on_entry_and_generated_on(session, seeded_user):
    entry = _seed_entry(session, seeded_user)
    assert entry.id is not None
    today = datetime.now(UTC).date()

    first = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={"user_id": seeded_user.id, "question_count": 1, "option_count": 3, "generated_on": today},
    )
    session.add(first)
    session.commit()
    session.refresh(first)

    duplicate = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={"user_id": seeded_user.id, "question_count": 1, "option_count": 3, "generated_on": today},
    )
    session.add(duplicate)

    with pytest.raises(IntegrityError):
        session.commit()


def test_quiz_answer_unique_constraint_on_attempt_and_question(session, seeded_user):
    entry = _seed_entry(session, seeded_user)
    assert entry.id is not None
    today = datetime.now(UTC).date()
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={"user_id": seeded_user.id, "question_count": 1, "option_count": 3, "generated_on": today},
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    assert quiz_set.id is not None

    question = QuizQuestion.model_validate(
        QuizQuestionCreate(
            quiz_set_id=quiz_set.id,
            question_text="x",
            correct_option_index=0,
            order_index=0,
        )
    )
    session.add(question)
    session.commit()
    session.refresh(question)
    assert question.id is not None

    attempt = QuizAttempt.model_validate(
        QuizAttemptCreate(quiz_set_id=quiz_set.id, user_id=seeded_user.id, total_questions=1),
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None

    first = QuizAnswer.model_validate(
        QuizAnswerCreate(attempt_id=attempt.id, question_id=question.id, is_correct=True),
    )
    session.add(first)
    session.commit()

    duplicate = QuizAnswer.model_validate(
        QuizAnswerCreate(attempt_id=attempt.id, question_id=question.id, is_correct=False),
    )
    session.add(duplicate)

    with pytest.raises(IntegrityError):
        session.commit()


def test_quiz_set_user_id_foreign_key_targets_users_table():
    user_id_field = QuizSet.model_fields["user_id"]
    assert user_id_field.foreign_key == "users.id"  # pyright: ignore[reportAttributeAccessIssue]


def test_quiz_set_entry_id_foreign_key_targets_study_entries_table():
    entry_id_field = QuizSet.model_fields["entry_id"]
    assert entry_id_field.foreign_key == "study_entries.id"  # pyright: ignore[reportAttributeAccessIssue]


def test_quiz_question_quiz_set_id_foreign_key_targets_quiz_sets_table():
    quiz_set_id_field = QuizQuestion.model_fields["quiz_set_id"]
    assert quiz_set_id_field.foreign_key == "quiz_sets.id"  # pyright: ignore[reportAttributeAccessIssue]


def test_quiz_attempt_quiz_set_id_foreign_key_targets_quiz_sets_table():
    quiz_set_id_field = QuizAttempt.model_fields["quiz_set_id"]
    assert quiz_set_id_field.foreign_key == "quiz_sets.id"  # pyright: ignore[reportAttributeAccessIssue]


def test_quiz_answer_attempt_and_question_foreign_keys():
    assert QuizAnswer.model_fields["attempt_id"].foreign_key == "quiz_attempts.id"  # pyright: ignore[reportAttributeAccessIssue]
    assert QuizAnswer.model_fields["question_id"].foreign_key == "quiz_questions.id"  # pyright: ignore[reportAttributeAccessIssue]


def test_phase4_tables_appear_in_metadata():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        for table_name in ("quiz_sets", "quiz_questions", "quiz_attempts", "quiz_answers"):
            assert table_name in SQLModel.metadata.tables
            assert session.exec(select(QuizSet)).all() == []


# ---------------------------------------------------------------------------
# SRA-801 — InterviewPlan / InterviewTurn / QuizAttempt.cursor_on_plan
#   plus the two new ``users`` preference columns. Phase 8 marker is applied
#   to the new tests so the ``-m 'not phase8' -q`` regression gate keeps
#   Phase 4 green while the schema lands.
# ---------------------------------------------------------------------------


phase8 = pytest.mark.phase8  # noqa: E402  -- the marker alias lives below the Phase 4 marker


@phase8
def test_interview_plan_quiz_set_id_unique_constraint(session, seeded_user):
    from sra.db.models import InterviewPlan, InterviewPlanCreate

    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Photosynthesis",
        summary="light + dark reactions",
        raw_transcript="today I studied photosynthesis",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    today = datetime.now(UTC).date()
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={"user_id": seeded_user.id, "question_count": 1, "option_count": 3, "generated_on": today},
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    assert quiz_set.id is not None

    first = InterviewPlan.model_validate(
        InterviewPlanCreate(quiz_set_id=quiz_set.id, target_questions=2),
    )
    session.add(first)
    session.commit()

    duplicate = InterviewPlan.model_validate(
        InterviewPlanCreate(quiz_set_id=quiz_set.id, target_questions=2),
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()


@phase8
def test_interview_plan_outline_json_round_trips_through_sqlite(session, seeded_user):
    from sra.db.models import InterviewPlan, InterviewPlanCreate

    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Photosynthesis",
        summary="s",
        raw_transcript="t",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": seeded_user.id,
            "question_count": 1,
            "option_count": 2,
            "generated_on": datetime.now(UTC).date(),
        },
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    assert quiz_set.id is not None

    outline = [{"subtopic": "intro", "difficulty": "easy", "focus_prompt": "what"}]
    plan = InterviewPlan.model_validate(
        InterviewPlanCreate(quiz_set_id=quiz_set.id, outline=outline, target_questions=1),
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    assert plan.id is not None

    reloaded = session.exec(select(InterviewPlan).where(InterviewPlan.id == plan.id)).one()
    assert reloaded.outline == outline


@phase8
def test_interview_turn_role_check_constraint_rejects_unknown_value(session, seeded_user):
    from sra.db.models import InterviewPlan, InterviewPlanCreate, InterviewTurn, InterviewTurnCreate

    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Mitosis",
        summary="s",
        raw_transcript="t",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": seeded_user.id,
            "question_count": 1,
            "option_count": 2,
            "generated_on": datetime.now(UTC).date(),
        },
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    assert quiz_set.id is not None

    plan = InterviewPlan.model_validate(
        InterviewPlanCreate(quiz_set_id=quiz_set.id, target_questions=1),
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)

    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id,
        user_id=seeded_user.id,
        mode="interview",
        total_questions=1,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None

    bad = InterviewTurn.model_validate(
        InterviewTurnCreate(attempt_id=attempt.id, role="bogus", content="x"),
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()


@phase8
def test_interview_turn_role_accepts_documented_values(session, seeded_user):
    from sra.db.models import InterviewPlan, InterviewPlanCreate, InterviewTurn, InterviewTurnCreate

    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Mitosis",
        summary="s",
        raw_transcript="t",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": seeded_user.id,
            "question_count": 1,
            "option_count": 2,
            "generated_on": datetime.now(UTC).date(),
        },
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    plan = InterviewPlan.model_validate(
        InterviewPlanCreate(quiz_set_id=quiz_set.id, target_questions=1),
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id,
        user_id=seeded_user.id,
        mode="interview",
        total_questions=1,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None

    for role in ("ai", "user", "system_summary"):
        row = InterviewTurn.model_validate(
            InterviewTurnCreate(attempt_id=attempt.id, role=role, content="hello"),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.id is not None


@phase8
def test_interview_turn_created_at_defaults_to_timezone_aware_utcnow(session, seeded_user):
    from sra.db.models import InterviewTurn, InterviewTurnCreate

    turn = InterviewTurn.model_validate(
        InterviewTurnCreate(attempt_id=1, role="ai", content="q0"),
    )
    assert turn.created_at is not None
    assert turn.created_at.tzinfo is not None


@phase8
def test_interview_plan_create_rejects_server_owned_fields():
    from sra.db.models import InterviewPlanCreate

    with pytest.raises(ValidationError):
        InterviewPlanCreate(
            quiz_set_id=1,
            target_questions=2,
            id=1,  # pyright: ignore[reportCallIssue]
            created_at=datetime.now(UTC),  # pyright: ignore[reportCallIssue]
        )


@phase8
def test_interview_turn_create_rejects_server_owned_fields():
    from sra.db.models import InterviewTurnCreate

    with pytest.raises(ValidationError):
        InterviewTurnCreate(
            attempt_id=1,
            role="ai",
            content="x",
            id=99,  # pyright: ignore[reportCallIssue]
        )


@phase8
def test_interview_plan_quiz_set_id_foreign_key_targets_quiz_sets_table():
    from sra.db.models import InterviewPlan

    field = InterviewPlan.model_fields["quiz_set_id"]
    assert field.foreign_key == "quiz_sets.id"  # pyright: ignore[reportAttributeAccessIssue]


@phase8
def test_interview_turn_attempt_id_foreign_key_targets_quiz_attempts_table():
    from sra.db.models import InterviewTurn

    field = InterviewTurn.model_fields["attempt_id"]
    assert field.foreign_key == "quiz_attempts.id"  # pyright: ignore[reportAttributeAccessIssue]


@phase8
def test_quiz_attempt_cursor_on_plan_defaults_to_zero():
    attempt = QuizAttempt.model_validate(
        QuizAttemptCreate(quiz_set_id=1, user_id=1, total_questions=1, mode="interview"),
    )
    assert attempt.cursor_on_plan == 0


@phase8
def test_quiz_attempt_cursor_on_plan_server_default_zero_for_phase4_attempts(session, seeded_user):
    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Mitosis",
        summary="s",
        raw_transcript="t",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": seeded_user.id,
            "question_count": 1,
            "option_count": 2,
            "generated_on": datetime.now(UTC).date(),
        },
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    attempt = QuizAttempt(
        quiz_set_id=quiz_set.id,
        user_id=seeded_user.id,
        mode="web",
        total_questions=1,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    reloaded = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt.id)).one()
    assert reloaded.cursor_on_plan == 0


@phase8
def test_user_interview_question_count_default_matches_design():
    user = User.model_validate(UserCreate(email="x@y", display_name="x", google_sub="g"))
    assert user.interview_question_count == 6


@phase8
def test_user_interview_target_minutes_default_matches_design():
    user = User.model_validate(UserCreate(email="x@y", display_name="x", google_sub="g"))
    assert user.interview_target_minutes == 5


@phase8
@pytest.mark.parametrize("count", [3, 6, 12])
def test_user_interview_question_count_accepts_in_range(count):
    user = User.model_validate(
        UserCreate(email="x@y", display_name="x", google_sub="g"),
        update={"interview_question_count": count},
    )
    assert user.interview_question_count == count


@phase8
@pytest.mark.parametrize("count", [2, 13, 0, 99])
def test_user_interview_question_count_rejects_out_of_range(count):
    with pytest.raises(ValidationError):
        User.model_validate(
            UserCreate(email="x@y", display_name="x", google_sub="g"),
            update={"interview_question_count": count},
        )


@phase8
@pytest.mark.parametrize("minutes", [1, 5, 20])
def test_user_interview_target_minutes_accepts_in_range(minutes):
    user = User.model_validate(
        UserCreate(email="x@y", display_name="x", google_sub="g"),
        update={"interview_target_minutes": minutes},
    )
    assert user.interview_target_minutes == minutes


@phase8
@pytest.mark.parametrize("minutes", [0, 21, 99])
def test_user_interview_target_minutes_rejects_out_of_range(minutes):
    with pytest.raises(ValidationError):
        User.model_validate(
            UserCreate(email="x@y", display_name="x", google_sub="g"),
            update={"interview_target_minutes": minutes},
        )


@phase8
def test_user_interview_question_count_check_constraint_in_db(session, seeded_user):
    seeded_user.interview_question_count = 0
    session.add(seeded_user)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@phase8
def test_user_interview_target_minutes_check_constraint_in_db(session, seeded_user):
    seeded_user.interview_target_minutes = 99
    session.add(seeded_user)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@phase8
def test_phase8_tables_appear_in_metadata():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine):
        for table_name in ("interview_plans", "interview_turns"):
            assert table_name in SQLModel.metadata.tables