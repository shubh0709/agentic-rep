"""add quiz phase 4 tables

Revision ID: a1b2c3d4e5f6
Revises: 8cb1b420d9d2
Create Date: 2026-10-14 10:30:00.000000

Phase 4 introduces the four MCQ quiz tables.  Every QuizSet points at the
existing study_entry the quiz was generated for; every QuizAttempt points at
the existing QuizSet it is grading; every QuizAnswer points at the existing
QuizAttempt and QuizQuestion it records.  Ownership is denormalized on
``quiz_sets.user_id`` so listing today's quizzes for a user is a single indexed
scan; the unique ``(entry_id, generated_on)`` constraint makes nightly
pregeneration idempotent; the unique ``(attempt_id, question_id)`` constraint
enforces one answer per question per attempt.

Downgrade drops the four new tables only -- Phase 1-3 rows in ``users``,
``study_entries``, and ``review_schedules`` remain untouched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "8cb1b420d9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the four quiz tables and their supporting indexes/constraints."""
    op.create_table(
        "quiz_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("generated_on", sa.Date(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("option_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["study_entries.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "generated_on", name="uq_quiz_sets_entry_generated_on"),
    )
    op.create_index(op.f("ix_quiz_sets_user_id"), "quiz_sets", ["user_id"], unique=False)

    op.create_table(
        "quiz_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quiz_set_id", sa.Integer(), nullable=False),
        sa.Column("question_text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("correct_option_index", sa.Integer(), nullable=False),
        sa.Column("explanation", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["quiz_set_id"], ["quiz_sets.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quiz_questions_quiz_set_id"), "quiz_questions", ["quiz_set_id"], unique=False)

    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quiz_set_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="web"),
        sa.Column(
            "started_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("completed_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["quiz_set_id"], ["quiz_sets.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quiz_attempts_user_id"), "quiz_attempts", ["user_id"], unique=False)

    op.create_table(
        "quiz_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("selected_option_index", sa.Integer(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column(
            "answered_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["attempt_id"], ["quiz_attempts.id"], ),
        sa.ForeignKeyConstraint(["question_id"], ["quiz_questions.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_quiz_answers_attempt_question"),
    )
    op.create_index(op.f("ix_quiz_answers_attempt_id"), "quiz_answers", ["attempt_id"], unique=False)


def downgrade() -> None:
    """Drop the four quiz tables; pre-Phase-4 rows in users, study_entries, and review_schedules are preserved."""
    op.drop_table("quiz_answers")
    op.drop_table("quiz_attempts")
    op.drop_table("quiz_questions")
    op.drop_table("quiz_sets")
