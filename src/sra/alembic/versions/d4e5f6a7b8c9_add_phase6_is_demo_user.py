"""add phase 6 is_demo_user column

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-14 17:00:00.000000

Phase 6 (SRA-601) marks every ``users`` row that was created by the public
demo instance with ``is_demo_user = true``. The reset job (SRA-603) filters
on this column to decide which rows are eligible for nightly cleanup.

``server_default = false`` lets the migration land cleanly over pre-Phase-6
rows: every existing user keeps ``is_demo_user = False`` without an explicit
data migration. The OAuth path never writes the flag, so a real self-hoster
can never be accidentally marked as demo.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the ``is_demo_user`` boolean column to ``users``."""
    op.add_column(
        "users",
        sa.Column(
            "is_demo_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Drop the ``is_demo_user`` column from ``users``."""
    op.drop_column("users", "is_demo_user")
