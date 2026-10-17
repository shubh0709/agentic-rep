"""add phase 9 reference materials table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-16 16:09:00.000000

Phase 9 (SRA-901) introduces the ``reference_materials`` table that
backs the topic-authoring ``POST /api/v1/topics`` route and the
``GET /api/v1/entries/{entry_id}/references`` read endpoint. Each
StudyEntry gains one or more references whose ``kind`` is one of
``url``, ``paste``, or ``upload``; the SQL CHECK constraint
``ck_reference_materials_kind`` enforces the discriminator so a
typo at the call site cannot produce a row with mismatched fields.
The ``entry_id`` FK is declared with ``ondelete="CASCADE"`` so
Postgres clears the dependent rows automatically; SQLite test runs
rely on ``sra.db.repository.delete_user_cascade`` for the same
outcome when FK enforcement is disabled.

Pre-Phase-9 rows are preserved untouched on both upgrade and
downgrade -- the migration only adds new objects.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``reference_materials`` table with its CHECK + index + FK."""

    op.create_table(
        "reference_materials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=True),
        sa.Column("pasted_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("file_name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("content_type", sqlmodel.sql.sqltypes.AutoString(length=127), nullable=True),
        sa.Column("extracted_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "fetch_status",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "fetch_error",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=True,
        ),
        sa.Column("fetched_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column(
            "created_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["study_entries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(kind = 'url' AND url IS NOT NULL AND pasted_text IS NULL AND file_name IS NULL) "
            "OR (kind = 'paste' AND pasted_text IS NOT NULL AND url IS NULL AND file_name IS NULL) "
            "OR (kind = 'upload' AND file_name IS NOT NULL AND content_type IS NOT NULL "
            "    AND url IS NULL AND pasted_text IS NULL)",
            name="ck_reference_materials_kind",
        ),
    )
    op.create_index(
        op.f("ix_reference_materials_entry_id"),
        "reference_materials",
        ["entry_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``reference_materials`` table and its index."""

    op.drop_index(op.f("ix_reference_materials_entry_id"), table_name="reference_materials")
    op.drop_table("reference_materials")