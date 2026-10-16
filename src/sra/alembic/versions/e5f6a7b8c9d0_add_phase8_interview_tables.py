"""add phase 8 interview tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-15 17:56:55.000000

Phase 8 (SRA-801) introduces the two interactive-AI-interview tables and the
three supporting columns:

  * ``interview_plans`` -- one outline per ``QuizSet``; ``quiz_set_id`` is
    unique so the planner is idempotent. ``outline`` is a JSON list of
    ``{subtopic, difficulty, focus_prompt}`` dicts; ``target_questions``
    snapshots the user's preference at plan-generation time.
  * ``interview_turns`` -- chronological log of AI / user / system_summary
    messages per ``QuizAttempt``; ``role`` is constrained to those three
    values via a ``CheckConstraint`` so a typo at the call site cannot
    silently land in the transcript.
  * ``quiz_attempts.cursor_on_plan`` -- mirrors the SRA-504 ``cursor``
    column for the interview outline index; the two are mutually exclusive
    per attempt because ``QuizAttempt.mode`` is set at start time.
  * ``users.interview_question_count`` and ``users.interview_target_minutes``
    -- the two new Phase 8 preferences, bounded by the
    ``ck_users_interview_preferences_in_range`` CHECK constraint.

Defaults (6 questions / 5 minutes) match the documented design so pre-Phase-8
rows pick up the same values on upgrade. ``cursor_on_plan`` defaults to 0 so
the existing Phase 4/5/6 attempts remain valid without any data migration.
Pre-Phase-8 rows are preserved untouched on both upgrade and downgrade.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the two interview tables and add the three new columns."""
    op.create_table(
        "interview_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quiz_set_id", sa.Integer(), nullable=False),
        sa.Column("outline", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("target_questions", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["quiz_set_id"], ["quiz_sets.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quiz_set_id", name="uq_interview_plans_quiz_set_id"),
    )

    op.create_table(
        "interview_turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("content", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("outline_index", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["attempt_id"], ["quiz_attempts.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('ai', 'user', 'system_summary')",
            name="ck_interview_turns_role",
        ),
    )
    op.create_index(op.f("ix_interview_turns_attempt_id"), "interview_turns", ["attempt_id"], unique=False)

    with op.batch_alter_table("quiz_attempts") as batch_op:
        batch_op.add_column(
            sa.Column("cursor_on_plan", sa.Integer(), server_default="0", nullable=False)
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "interview_question_count",
                sa.Integer(),
                server_default="6",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "interview_target_minutes",
                sa.Integer(),
                server_default="5",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_users_interview_preferences_in_range",
            "interview_question_count BETWEEN 3 AND 12 "
            "AND interview_target_minutes BETWEEN 1 AND 20",
        )


def downgrade() -> None:
    """Drop the two new tables and the three new columns in reverse order."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_interview_preferences_in_range", type_="check")
        batch_op.drop_column("interview_target_minutes")
        batch_op.drop_column("interview_question_count")

    with op.batch_alter_table("quiz_attempts") as batch_op:
        batch_op.drop_column("cursor_on_plan")

    op.drop_index(op.f("ix_interview_turns_attempt_id"), table_name="interview_turns")
    op.drop_table("interview_turns")
    op.drop_table("interview_plans")
