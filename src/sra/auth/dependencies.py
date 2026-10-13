"""
Auth dependencies. Concrete for Phase 1 -- there's no login flow yet,
so every request is treated as a single local user, auto-created on
first use. Phase 3 replaces get_current_user's body with real Google
OAuth + session/RBAC (require_user / require_admin, per the design
doc's KTD9/KTD10); every route is already written against this same
function name and return type, so that swap won't touch route code.
"""

from fastapi import Depends
from sqlmodel import Session

from sra.db.models import User
from sra.db.session import get_session


def get_current_user(session: Session = Depends(get_session)) -> User:
    """Return the single local user, creating it on first call if none exists yet."""
    raise NotImplementedError
