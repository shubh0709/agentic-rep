"""
DB engine/session setup. Concrete (not a learning stub) -- this is
plumbing, not something this project is meant to teach.
"""

from collections.abc import Generator
from functools import lru_cache

from sqlmodel import Session, SQLModel, create_engine

from sra.config import get_settings


@lru_cache
def get_engine():
    """Create (and cache) the SQLModel engine for the configured DATABASE_URL."""
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, connect_args=connect_args)


def init_db() -> None:
    """Create all tables. Call once at app startup (or from a test fixture)."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one DB session per request."""
    with Session(get_engine()) as session:
        yield session
