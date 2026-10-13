"""
Shared FastAPI dependencies, re-exported from their owning packages so
routers import one name from one place (`sra.api.deps`) instead of
reaching into `sra.db.session` / `sra.auth.dependencies` individually.
Re-exporting by import (not wrapping) preserves object identity, which
matters for `app.dependency_overrides[get_session] = ...` in tests.
"""

from sra.auth.dependencies import get_current_user
from sra.db.session import get_session

__all__ = ["get_current_user", "get_session"]
