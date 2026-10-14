from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import FastAPI

from sra.api.rate_limit import RateLimitState
from sra.api.routers import api_router
from sra.config import get_settings
from sra.db.session import init_db
from sra.jobs.digest_scheduler import start_scheduler, stop_scheduler

API_V1_PREFIX = "/api/v1"


def create_app(*, rate_limit_clock: Callable[[], float] = monotonic) -> FastAPI:
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

    limits = RateLimitState.from_settings(get_settings(), rate_limit_clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            start_scheduler(app)
            yield
        finally:
            stop_scheduler(app)
            limits.reset()

    init_db()
    app = FastAPI(title="Spaced Repetition Agent", lifespan=lifespan)
    app.state.rate_limits = limits
    app.include_router(api_router, prefix=API_V1_PREFIX)

    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
