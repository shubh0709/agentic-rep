"""add phase 11 assessment sessions and interview rationale

Revision ID: 1a2b3c4d5e6f
Revises: 0707180a9b21
Create Date: 2026-10-24 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "0707180a9b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("interview_turns", sa.Column("rationale", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_table(
        "assessment_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("topic", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("selected_topic_keys", sa.JSON(), nullable=False),
        sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("mcq_question_count", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("interview_question_count", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="draft"),
        sa.Column("quiz_set_id", sa.Integer(), nullable=True),
        sa.Column("interview_plan_id", sa.Integer(), nullable=True),
        sa.Column("mcq_attempt_id", sa.Integer(), nullable=True),
        sa.Column("interview_attempt_id", sa.Integer(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("report_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("completed_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.CheckConstraint("mode IS NULL OR mode IN ('mcq', 'interview', 'both')", name="ck_assessment_sessions_mode"),
        sa.CheckConstraint("mcq_question_count BETWEEN 1 AND 20 AND interview_question_count BETWEEN 3 AND 12", name="ck_assessment_sessions_counts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["entry_id"], ["study_entries.id"]),
        sa.ForeignKeyConstraint(["quiz_set_id"], ["quiz_sets.id"]),
        sa.ForeignKeyConstraint(["interview_plan_id"], ["interview_plans.id"]),
        sa.ForeignKeyConstraint(["mcq_attempt_id"], ["quiz_attempts.id"]),
        sa.ForeignKeyConstraint(["interview_attempt_id"], ["quiz_attempts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assessment_sessions_user_id"), "assessment_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_assessment_sessions_entry_id"), "assessment_sessions", ["entry_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_assessment_sessions_entry_id"), table_name="assessment_sessions")
    op.drop_index(op.f("ix_assessment_sessions_user_id"), table_name="assessment_sessions")
    op.drop_table("assessment_sessions")
    op.drop_column("interview_turns", "rationale")
