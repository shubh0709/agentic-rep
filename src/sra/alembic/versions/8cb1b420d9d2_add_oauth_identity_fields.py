"""add OAuth identity fields

Revision ID: 8cb1b420d9d2
Revises: 067a64508521
Create Date: 2026-10-14 00:00:00.000000

Legacy rows intentionally retain a NULL google_sub.  The application only
creates OAuth-backed rows after provider verification, while an operator may
explicitly link a preserved row with ``python -m sra.db.legacy_identity``.

Downgrade removes google_sub, role, and last_login_at.  Those identity and
authorization values cannot be recovered after downgrade, but user IDs and all
entry/review ownership remain intact.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision: str = "8cb1b420d9d2"
down_revision: Union[str, Sequence[str], None] = "067a64508521"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable legacy identity plus least-privilege authorization fields."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("google_sub", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "role",
                sqlmodel.sql.sqltypes.AutoString(),
                server_default="user",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("last_login_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))
        batch_op.create_check_constraint("ck_users_role", "role IN ('user', 'admin')")
        batch_op.create_check_constraint(
            "ck_users_google_sub_nonempty",
            "google_sub IS NULL OR length(trim(google_sub)) > 0",
        )
        batch_op.create_index("ix_users_google_sub", ["google_sub"], unique=True)


def downgrade() -> None:
    """Remove Phase 3 identity data; IDs and ownership rows are preserved."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_google_sub")
        batch_op.drop_constraint("ck_users_google_sub_nonempty", type_="check")
        batch_op.drop_constraint("ck_users_role", type_="check")
        batch_op.drop_column("last_login_at")
        batch_op.drop_column("role")
        batch_op.drop_column("google_sub")
