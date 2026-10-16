"""
DB engine/session setup. Concrete (not a learning stub) -- this is
plumbing, not something this project is meant to teach.
"""

import re
from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

import sqlalchemy
import sqlalchemy.exc
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import event as sa_event
from sqlalchemy import inspect
from sqlmodel import Session, create_engine

from sra.config import get_settings

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = Path(__file__).resolve().parents[1] / "alembic"
# The very first revision in the chain. Used to repair databases that
# pre-date Alembic (no ``alembic_version`` table but tables that look
# like the initial schema): stamp them at base so a subsequent
# ``upgrade head`` knows where to start.
INITIAL_REVISION = "067a64508521"


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
    """Bring the configured database to the latest Alembic revision.

    Runs ``alembic upgrade head`` against the configured ``DATABASE_URL`` so:

    * a fresh SQLite file is created from the full migration chain (the
      initial schema revision plus every subsequent one), and
    * an existing SQLite file left over from a prior release is upgraded
      in place: every column, CHECK constraint, index, and new table
      from the intervening revisions is added.

    ``SQLModel.metadata.create_all`` is not enough here. It only creates
    tables that do not yet exist -- it never adds a new column to a
    table a developer created against an earlier model. A local
    ``sra.db`` from before Phase 5 would still be missing
    ``users.preferred_review_time`` after a ``create_all`` call, and
    the very next ``SELECT users.email, ..., users.preferred_review_time``
    would raise ``sqlite3.OperationalError: no such column``. The
    Alembic migration chain in ``src/sra/alembic/versions/`` is the
    single source of truth for schema evolution, so this function
    delegates to it instead. The Docker entrypoint
    (``docker/entrypoint.sh``) already follows the same path; this keeps
    local ``uvicorn`` startup and the containerized startup in lockstep.

    A pre-Alembic database -- one created by the original
    ``SQLModel.metadata.create_all`` before migrations were introduced
    -- has tables but no ``alembic_version`` row, so Alembic cannot
    know where to start. For that case we stamp the database at the
    initial revision first; the subsequent ``upgrade head`` then walks
    forward and adds every later column, table, index, and constraint.
    """
    settings = get_settings()
    config = AlembicConfig(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", settings.database_url)

    engine = get_engine()
    with engine.connect() as conn:
        existing_tables = set(inspect(conn).get_table_names())
    if "alembic_version" not in existing_tables and "users" in existing_tables:
        alembic_command.stamp(config, INITIAL_REVISION)

    try:
        alembic_command.upgrade(config, "head")
    except sqlalchemy.exc.OperationalError as exc:
        # The configured database is in an inconsistent state -- Alembic
        # is about to (or just did) emit a CREATE that collides with a
        # table or column that already exists, but the ``alembic_version``
        # row says a prior revision is current. This is the failure mode
        # that the original ``SQLModel.metadata.create_all`` left behind
        # when migrations were bolted on after the fact: an early
        # ``create_all`` wrote some tables, a partial run wrote others,
        # and the version row landed in the middle. ``alembic upgrade``
        # cannot recover without dropping the conflicting object, which
        # would lose data. Re-raise with a clear, actionable message
        # instead of the raw SQLite error.
        message = str(exc).lower()
        if "already exists" in message:
            raise RuntimeError(
                f"Database at {settings.database_url!r} is in an inconsistent "
                "state: its schema objects do not match the revision recorded "
                "in ``alembic_version``. This happens when a prior ``create_all`` "
                "or partial migration left objects behind. ``alembic upgrade`` "
                "cannot reconcile it without dropping data. For local "
                "development, delete the SQLite file and restart the app "
                "(``init_db`` will recreate the full schema), or call "
                "``sra.db.session.reset_db()`` to drop and recreate the "
                "schema in place. For a production database, restore from a "
                "backup taken before the inconsistent migration."
            ) from exc
        raise
    # Drop any cached engine so the next caller re-resolves ``DATABASE_URL``
    # and picks up the schema we just installed. Without this, a process
    # that switched ``DATABASE_URL`` between two ``init_db`` calls would
    # still hand out the old engine.
    get_engine.cache_clear()


def reset_db() -> None:
    """Drop every table in the configured database and re-run all migrations.

    This is the local-development recovery path for a database that is
    in an inconsistent state -- one where the ``alembic_version`` row
    points at a revision whose schema no longer matches the tables on
    disk. ``init_db`` cannot repair such a database without losing
    data, so it refuses to run the upgrade and points the developer
    here. ``reset_db`` drops every table (including
    ``alembic_version`` and any orphan tables the migration chain
    never knew about) and then runs ``alembic upgrade head`` from
    scratch, so the database is back at ``head`` and the next
    ``init_db`` is a no-op.

    We do not use ``alembic downgrade base`` here: when the database
    is inconsistent, the downgrade path itself can fail because
    Alembic does not know about the orphan tables. A raw
    ``DROP TABLE`` for every object is the only safe ground truth.

    Never call this against a production database. It deletes every
    row in every table; there is no confirmation prompt. The Docker
    entrypoint does not call this -- it relies on ``alembic upgrade``
    working, which is the contract for a real deployment.
    """
    settings = get_settings()
    engine = get_engine()
    with engine.begin() as connection:
        inspector = inspect(connection)
        table_names = inspector.get_table_names()
        # SQLite needs foreign-key checks off while we drop tables that
        # reference each other; otherwise a table that points at a
        # later one in the drop order fails the check.
        connection.execute(sqlalchemy.text("PRAGMA foreign_keys = OFF"))
        for table_name in table_names:
            connection.execute(
                sqlalchemy.text(f'DROP TABLE IF EXISTS "{table_name}"')
            )
        connection.execute(sqlalchemy.text("PRAGMA foreign_keys = ON"))
    get_engine.cache_clear()

    config = AlembicConfig(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    alembic_command.upgrade(config, "head")
    get_engine.cache_clear()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one DB session per request."""
    with Session(get_engine()) as session:
        yield session
