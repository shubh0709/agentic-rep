"""add phase 5 quiz_attempt cursor column

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-14 14:30:00.000000

Phase 5 voice quiz conversation graph (SRA-504) tracks which question an
attempt is currently on with a per-attempt ``cursor``. Defaults to 0 so the
existing Phase 4 quiz attempts remain valid without any data migration;
the column is server-owned and only ``process_voice_turn`` mutates it
(via ``advance_quiz_cursor`` and ``complete_quiz_attempt``).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the cursor column to quiz_attempts with a server default of 0."""
    with op.batch_alter_table("quiz_attempts") as batch_op:
        batch_op.add_column(
            sa.Column("cursor", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    """Drop the cursor column from quiz_attempts."""
    with op.batch_alter_table("quiz_attempts") as batch_op:
        batch_op.drop_column("cursor")
