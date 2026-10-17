"""add phase 9 mcq quality columns

Revision ID: 0707180a9b21
Revises: f6a7b8c9d0e1
Create Date: 2026-10-16 17:00:00.000000

Phase 9 (SRA-903) extends the SRA-402 quiz schema with three additive
nullable columns on ``quiz_questions`` and a JSON-column append-only
``discarded_for_evidence`` log on ``quiz_sets``. The new columns capture
the verify-pass evidence trail (Bloom level, source passage quote,
distractor misconception) and the per-set list of verify-rejected
drafts.

Both objects are strictly additive: pre-Phase-9 quiz rows survive the
upgrade because every new column is nullable. The downgrade drops the
columns in reverse order so a downgrade-then-upgrade round trip
restores the schema and the pre-existing quiz row unchanged.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "0707180a9b21"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the three Phase 9 quiz question columns and the QuizSet
    verify-discard log.

    Every new column is nullable so pre-Phase-9 quiz rows survive the
    upgrade intact; the ``discarded_for_evidence`` JSON column carries
    a server-side ``[]`` default so existing readers always see a
    list rather than NULL.
    """

    with op.batch_alter_table("quiz_questions") as batch_op:
        batch_op.add_column(
            sa.Column("bloom_level", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "source_passage_quote",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "distractor_misconception",
                sqlmodel.sql.sqltypes.AutoString(length=500),
                nullable=True,
            )
        )

    with op.batch_alter_table("quiz_sets") as batch_op:
        batch_op.add_column(
            sa.Column(
                "discarded_for_evidence",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    """Drop the Phase 9 columns. Pre-Phase-9 quiz rows remain readable
    -- the columns are nullable, so dropping them never fails on the
    row payload.
    """

    with op.batch_alter_table("quiz_sets") as batch_op:
        batch_op.drop_column("discarded_for_evidence")

    with op.batch_alter_table("quiz_questions") as batch_op:
        batch_op.drop_column("distractor_misconception")
        batch_op.drop_column("source_passage_quote")
        batch_op.drop_column("bloom_level")