from fastapi import FastAPI

from sra.api.routers import api_router
from sra.db.session import init_db

API_V1_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    """
    Build and return the FastAPI app.

    Steps:
      1. Call init_db() (imported above) so a fresh SQLite file gets its
         tables -- fine for tests/local dev; a real deployment applies
         `alembic upgrade head` instead (see alembic.ini / src/sra/alembic/),
         since create_all() can't reconcile schema changes made on
         separate branches the way a migration chain can.
      2. Construct `app = FastAPI(title="Spaced Repetition Agent")`.
      3. `app.include_router(api_router, prefix=API_V1_PREFIX)` (imported
         above) -- api_router already aggregates all four resource routers,
         so app.py doesn't need to know about them individually.
      4. Return app.

    This is a factory (not a module-level `app = FastAPI()`) so tests
    can call it repeatedly against a fresh/overridden DB session --
    see tests/unit/test_api_routes.py for the dependency-override
    pattern that relies on this, and tests/conftest.py's `client`
    fixture for why `init_db` is imported by name here (it monkeypatches
    `sra.api.app.init_db` directly rather than touching the real DB file).
    """

    init_db()
    app = FastAPI(title="Spaced Repetition Agent")
    app.include_router(api_router, prefix=API_V1_PREFIX)
    return app
