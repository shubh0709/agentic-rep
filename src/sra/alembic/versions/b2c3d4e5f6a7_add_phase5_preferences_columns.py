"""add phase 5 preferences columns

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-14 13:56:00.000000

Phase 5 introduces five per-user columns the wake-up loop, the pregeneration
job, and the voice quiz conversation graph all read:

  * ``preferred_review_time`` -- HH:MM string, validated by the model
    ``field_validator`` and by the ``ck_users_preferred_review_time_shape``
    CHECK constraint installed below.
  * ``timezone`` -- IANA zone name, validated by the model ``field_validator``
    via ``zoneinfo.ZoneInfo``.
  * ``quiz_question_count`` / ``quiz_option_count`` -- integer counts, bounded
    by the ``ck_users_quiz_counts_positive`` CHECK constraint.
  * ``wakeup_notification_sent_at`` -- nullable timezone-aware datetime
    flipped exclusively by the wake-up scheduler (SRA-502). Cleared back to
    NULL at midnight in the user's local zone.

Defaults match the Phase 4 hard-coded values so behavior is preserved for
rows that have not been edited. Downgrade removes the constraint and the
five columns in reverse order; pre-Phase-5 rows are preserved untouched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the five Phase 5 preference columns plus their CHECK constraints."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "preferred_review_time",
                sqlmodel.sql.sqltypes.AutoString(),
                server_default="09:00",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "timezone",
                sqlmodel.sql.sqltypes.AutoString(),
                server_default="UTC",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("quiz_question_count", sa.Integer(), server_default="10", nullable=False)
        )
        batch_op.add_column(
            sa.Column("quiz_option_count", sa.Integer(), server_default="4", nullable=False)
        )
        batch_op.add_column(
            sa.Column("wakeup_notification_sent_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_users_preferred_review_time_shape",
            "preferred_review_time REGEXP '^[0-2][0-9]:[0-5][0-9]$'",
        )
        batch_op.create_check_constraint(
            "ck_users_quiz_counts_positive",
            "quiz_question_count BETWEEN 1 AND 20 AND quiz_option_count BETWEEN 2 AND 6",
        )


def downgrade() -> None:
    """Remove the Phase 5 preference columns and their CHECK constraints."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_quiz_counts_positive", type_="check")
        batch_op.drop_constraint("ck_users_preferred_review_time_shape", type_="check")
        batch_op.drop_column("wakeup_notification_sent_at")
        batch_op.drop_column("quiz_option_count")
        batch_op.drop_column("quiz_question_count")
        batch_op.drop_column("timezone")
        batch_op.drop_column("preferred_review_time")