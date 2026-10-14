"""FastAPI authentication and role dependencies."""

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from sra.auth.google_oauth import AuthError, verify_app_credential
from sra.config import Settings, get_settings
from sra.db.models import User, UserRole
from sra.db.session import get_session


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_user(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    """Resolve exactly one signed cookie or bearer credential to a local user."""
    authorization = request.headers.get("Authorization")
    cookie = request.cookies.get(settings.auth_cookie_name)
    bearer: str | None = None
    if authorization is not None:
        scheme, separator, value = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not value.strip():
            raise _unauthorized()
        bearer = value.strip()

    if cookie and bearer:
        raise _unauthorized()
    credential = bearer or cookie
    if not credential:
        raise _unauthorized()

    try:
        claims = verify_app_credential(
            credential,
            signing_secret=settings.auth_signing_secret.get_secret_value(),
            now=datetime.now(UTC),
        )
    except AuthError as error:
        raise _unauthorized() from error
    user = session.get(User, claims.user_id)
    if user is None or user.google_sub is None:
        raise _unauthorized()
    request.state.user = user
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    """Require the authenticated user to carry the administrator role."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user


# Compatibility alias for earlier imports and dependency overrides.  It is the
# same function object, so overrides retain their identity during the migration.
get_current_user = require_user
