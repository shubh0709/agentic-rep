"""SRA-401 quiz-schema contracts, written before the migration lands."""

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
)

pytestmark = pytest.mark.phase4


def _seed_entry(session: Session, user: User) -> StudyEntry:
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
        QuizSetCreate(entry_id=1, user_id=1, generated_on=datetime.now(UTC).date())


def test_quiz_question_create_rejects_server_owned_fields():
    with pytest.raises(ValidationError):
        QuizQuestionCreate(quiz_set_id=1, question_text="x", correct_option_index=0, order_index=0, id=99)


def test_quiz_attempt_create_rejects_server_owned_fields():
    with pytest.raises(ValidationError):
        QuizAttemptCreate(
            quiz_set_id=1,
            user_id=1,
            total_questions=3,
            started_at=datetime.now(UTC),
        )


def test_quiz_answer_create_rejects_server_owned_fields():
    with pytest.raises(ValidationError):
        QuizAnswerCreate(
            attempt_id=1,
            question_id=1,
            is_correct=True,
            answered_at=datetime.now(UTC),
        )


def test_quiz_question_options_json_round_trips_through_sqlite(session, seeded_user):
    entry = _seed_entry(session, seeded_user)
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
    assert user_id_field.foreign_key == "users.id"


def test_quiz_set_entry_id_foreign_key_targets_study_entries_table():
    entry_id_field = QuizSet.model_fields["entry_id"]
    assert entry_id_field.foreign_key == "study_entries.id"


def test_quiz_question_quiz_set_id_foreign_key_targets_quiz_sets_table():
    quiz_set_id_field = QuizQuestion.model_fields["quiz_set_id"]
    assert quiz_set_id_field.foreign_key == "quiz_sets.id"


def test_quiz_attempt_quiz_set_id_foreign_key_targets_quiz_sets_table():
    quiz_set_id_field = QuizAttempt.model_fields["quiz_set_id"]
    assert quiz_set_id_field.foreign_key == "quiz_sets.id"


def test_quiz_answer_attempt_and_question_foreign_keys():
    assert QuizAnswer.model_fields["attempt_id"].foreign_key == "quiz_attempts.id"
    assert QuizAnswer.model_fields["question_id"].foreign_key == "quiz_questions.id"


def test_phase4_tables_appear_in_metadata():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        for table_name in ("quiz_sets", "quiz_questions", "quiz_attempts", "quiz_answers"):
            assert table_name in SQLModel.metadata.tables
            assert session.exec(select(QuizSet)).all() == []