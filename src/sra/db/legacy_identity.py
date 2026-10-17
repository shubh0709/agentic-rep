"""Explicit operator tooling for attaching pre-OAuth data to an identity."""

import argparse

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from sra.db.models import User
from sra.db.session import get_engine


class LegacyIdentityError(ValueError):
    """The requested legacy-user link is unsafe or cannot be performed."""


def link_legacy_user(*, user_id: int, google_sub: str, session: Session) -> User:
    """Attach one operator-selected legacy row to a known Google subject.

    This function is deliberately never called by a request handler.  It keeps
    the existing numeric ID, so entry ownership and review schedules remain
    unchanged, and refuses to overwrite an identity that is already linked.
    """
    if not google_sub.strip():
        raise LegacyIdentityError("google_sub must be non-empty")

    # This operator command is part of the Phase 3 migration procedure and may
    # intentionally run while the database is still at that historical
    # revision. Loading the current ORM model would SELECT columns introduced
    # by later phases and fail before the identity can be linked. Restrict the
    # query/update to the columns owned by the Phase 3 contract instead.
    row = session.execute(
        text(
            "SELECT id, email, display_name, google_sub, role, last_login_at, "
            "created_at FROM users WHERE id = :user_id"
        ),
        {"user_id": user_id},
    ).mappings().one_or_none()
    if row is None:
        raise LegacyIdentityError(f"user_id {user_id} does not exist")
    if row["google_sub"] is not None:
        raise LegacyIdentityError(f"user_id {user_id} is already linked")

    try:
        session.execute(
            text("UPDATE users SET google_sub = :google_sub WHERE id = :user_id"),
            {"google_sub": google_sub, "user_id": user_id},
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise LegacyIdentityError("google_sub is already linked to another user") from error

    return User.model_validate({**dict(row), "google_sub": google_sub})


def main() -> None:
    """Link a legacy row using the configured DATABASE_URL."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--google-sub", required=True)
    args = parser.parse_args()

    with Session(get_engine()) as session:
        user = link_legacy_user(user_id=args.user_id, google_sub=args.google_sub, session=session)
    print(f"Linked user_id={user.id} to the supplied Google subject.")


if __name__ == "__main__":
    main()
