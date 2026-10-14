"""Explicit operator tooling for attaching pre-OAuth data to an identity."""

import argparse

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

    user = session.get(User, user_id)
    if user is None:
        raise LegacyIdentityError(f"user_id {user_id} does not exist")
    if user.google_sub is not None:
        raise LegacyIdentityError(f"user_id {user_id} is already linked")

    user.google_sub = google_sub
    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise LegacyIdentityError("google_sub is already linked to another user") from error
    session.refresh(user)
    return user


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
