"""
DB engine/session setup. Concrete (not a learning stub) -- this is
plumbing, not something this project is meant to teach.
"""

import re
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import event as sa_event
from sqlmodel import Session, SQLModel, create_engine

from sra.config import get_settings


def _sqlite_regexp(pattern: str, value: str | None) -> int:
    """SQLite REGEXP callback. SQLite passes (pattern, value) and returns 0/1.

    Used by CheckConstraint expressions on `users.preferred_review_time` so the
    DB rejects any HH:MM shape that slips past the Pydantic field validator
    (e.g. writes that set the attribute directly on a `table=True` instance).
    """
    if value is None or pattern is None:
        return 0
    return 1 if re.fullmatch(pattern, value) is not None else 0


@lru_cache
def get_engine():
    """Create (and cache) the SQLModel engine for the configured DATABASE_URL."""
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    else:
        connect_args = {}
    engine = create_engine(settings.database_url, connect_args=connect_args)

    if engine.dialect.name == "sqlite":
        @sa_event.listens_for(engine, "connect")
        def _register_sqlite_regexp(dbapi_connection, _connection_record):  # noqa: ANN001
            dbapi_connection.create_function("regexp", 2, _sqlite_regexp)

    return engine


def init_db() -> None:
    """Create all tables. Call once at app startup (or from a test fixture)."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one DB session per request."""
    with Session(get_engine()) as session:
        yield session
