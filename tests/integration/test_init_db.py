"""Regression tests for ``sra.db.session.init_db``.

These tests exist to lock in the contract that
``init_db()`` brings the configured database to the latest Alembic
revision -- not just to "create missing tables" via
``SQLModel.metadata.create_all``. They cover four concrete failure
modes that an earlier ``create_all``-only implementation exhibited:

1. A brand-new SQLite file is created from the full migration chain
   (initial schema + every subsequent revision), so all tables and
   columns the current models expect exist before the first request
   is served.
2. A SQLite file left over from a prior release -- the
   "stale ``sra.db`` from before Phase 5" scenario that produced the
   ``no such column: users.preferred_review_time`` crash -- is
   upgraded in place so every later column lands without manual
   ``alembic upgrade`` intervention by the developer.
3. A pre-Alembic database -- one created by the original
   ``SQLModel.metadata.create_all`` before Alembic existed, with
   tables but no ``alembic_version`` row -- is stamped at the base
   revision and then upgraded to head.
4. After ``init_db()`` runs against any of the above, the ``User``
   SQLModel can be SELECTed with every attribute it declares,
   including ``preferred_review_time`` and ``wakeup_notification_sent_at``
   (the two columns the wake-up / preferences stack reads on every
   request). A direct ``Session.exec(select(User))`` against the file
   also catches the specific ``OperationalError`` the user reported.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

from sra.config import get_settings
from sra.db import models  # noqa: F401  -- register tables on SQLModel.metadata
from sra.db.models import User
from sra.db.session import get_engine, init_db, reset_db

ROOT = Path(__file__).resolve().parents[2]
INITIAL_REVISION = "067a64508521"

USER_MODEL_COLUMNS = {
    "id",
    "email",
    "display_name",
    "google_sub",
    "role",
    "preferred_review_time",
    "timezone",
    "quiz_question_count",
    "quiz_option_count",
    "is_demo_user",
    "last_login_at",
    "wakeup_notification_sent_at",
    "created_at",
}


def _tmp_database(tmp_path: Path) -> Path:
    try:
        return tmp_path / "init-db.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")


def _point_settings_at(monkeypatch: pytest.MonkeyPatch, database: Path) -> None:
    """Route ``get_settings().database_url`` at ``database`` for this test.

    The ``disable_live_tracing`` autouse fixture in ``tests/conftest.py``
    already cleared ``get_settings`` at the start of the test; this helper
    re-clears it after ``monkeypatch.setenv`` so the cached instance picks
    up the temp ``DATABASE_URL``. ``get_engine`` is cleared lazily inside
    ``init_db`` itself, but the test also clears it up front in case a
    previous test in the same process already cached an engine.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-init-db-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    get_engine.cache_clear()


def _user_columns(database: Path) -> set[str]:
    inspector = inspect(create_engine(f"sqlite:///{database}"))
    return {column["name"] for column in inspector.get_columns("users")}


def test_init_db_creates_full_schema_on_a_fresh_sqlite_file(monkeypatch, tmp_path):
    """A brand-new SQLite file gets every table and column the current models declare.

    Before ``init_db()`` was taught to delegate to Alembic, calling it
    against a fresh file would have run the same migration chain anyway
    via ``create_all``. This test pins that behavior in place: the
    full set of ``User`` columns must be present so subsequent tests
    can rely on it, and all dependent tables (``study_entries``,
    ``review_schedules``, ``quiz_sets``, ``quiz_questions``,
    ``quiz_attempts``, ``quiz_answers``) must also be created.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    init_db()

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert {"users", "study_entries", "review_schedules", "quiz_sets",
            "quiz_questions", "quiz_attempts", "quiz_answers"} <= tables
    assert USER_MODEL_COLUMNS <= _user_columns(database)


def test_init_db_upgrades_a_stale_database_to_head(monkeypatch, tmp_path):
    """The exact failure mode from the bug report: a stale ``sra.db`` from
    the initial schema is upgraded in place so all Phase 5 columns appear.

    The user reported
    ``sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such
    column: users.preferred_review_time`` because their local ``sra.db``
    was created by the original Phase 1 ``create_all`` and then sat
    through every Phase 2/3/4/5 migration locally. With the old
    ``init_db``, ``create_all`` was a no-op on a file that already
    contained a ``users`` table, so the new columns never landed.

    This test reproduces that exact stale file by running only the
    initial Alembic revision, then calls ``init_db()`` and asserts
    every ``User`` model column is present afterwards.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    alembic_config = Config(ROOT / "alembic.ini")
    alembic_config.set_main_option(
        "script_location", str(ROOT / "src" / "sra" / "alembic")
    )
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(alembic_config, INITIAL_REVISION)

    assert "preferred_review_time" not in _user_columns(database), (
        "test precondition: the initial schema must not already contain "
        "the Phase 5 column, otherwise the regression would not be exercised"
    )

    init_db()

    assert USER_MODEL_COLUMNS <= _user_columns(database), (
        "init_db() must add every User model column to a stale database "
        "so SELECTs against the model no longer raise "
        "'no such column: users.preferred_review_time'"
    )


def test_init_db_makes_user_selectable_with_full_model_columns(monkeypatch, tmp_path):
    """The exact ``OperationalError`` from the bug report must not recur.

    After ``init_db()`` runs -- whether against a fresh file or a stale
    one -- the very next ``SELECT users.email, ..., users.preferred_review_time,
    ..., users.wakeup_notification_sent_at, ...`` that the FastAPI request
    handlers issue must succeed. We exercise that path through the
    SQLModel ``User`` mapping rather than a hand-rolled query, so a
    future model addition that adds a column without a migration will
    also fail this test instead of crashing the first request after
    startup.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    init_db()

    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as session:
        rows = session.exec(select(User)).all()

    assert rows == [], "fresh database is empty"
    # And a raw SELECT that names every model column must also work,
    # mirroring the query the wake-up scheduler runs.
    raw_columns = sorted(USER_MODEL_COLUMNS - {"id"})
    quoted = ", ".join(f"users.{name}" for name in raw_columns)
    with engine.connect() as connection:
        connection.execute(text(f"SELECT {quoted} FROM users")).fetchall()
    engine.dispose()


def test_init_db_is_idempotent(monkeypatch, tmp_path):
    """Calling ``init_db()`` twice against the same file must be a no-op the second time.

    ``uvicorn`` can call ``init_db()`` more than once when the process
    restarts inside a watcher, and Alembic must not error or duplicate
    work in that case. This also locks in that ``init_db`` does not
    accidentally drop user data between runs.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    init_db()
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, google_sub, role, "
                "preferred_review_time, timezone, quiz_question_count, "
                "quiz_option_count, is_demo_user, last_login_at, "
                "wakeup_notification_sent_at, created_at) "
                "VALUES (1, 'kept@example.com', 'Kept', 'kept-sub', 'user', "
                "'09:00', 'UTC', 10, 4, 0, NULL, NULL, '2026-10-15 00:00:00')"
            )
        )
    engine.dispose()

    init_db()

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT email, preferred_review_time FROM users WHERE id = 1")
        ).one()
    engine.dispose()
    assert row == ("kept@example.com", "09:00"), (
        "a second init_db() call must not lose or rewrite existing rows"
    )


def test_init_db_is_wired_into_create_app(monkeypatch, tmp_path):
    """``create_app()`` must invoke ``init_db()`` so a developer running
    ``uvicorn sra.api.app:create_app --factory`` against an old ``sra.db``
    gets the same upgrade the Docker entrypoint runs.

    The ``client`` fixture in ``tests/conftest.py`` monkeypatches
    ``sra.api.app.init_db`` to a no-op so the in-memory test database
    is not touched. This test deliberately does not use that fixture
    and instead points ``DATABASE_URL`` at a temp file, then constructs
    the real app and confirms the resulting engine can SELECT every
    User model column -- which would fail with the original
    ``no such column`` error if ``init_db`` were not delegated to
    Alembic.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    alembic_config = Config(ROOT / "alembic.ini")
    alembic_config.set_main_option(
        "script_location", str(ROOT / "src" / "sra" / "alembic")
    )
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(alembic_config, INITIAL_REVISION)

    from sra.api.app import create_app

    create_app()

    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as session:
        session.exec(select(User)).all()
    engine.dispose()


def test_init_db_preserves_existing_user_data_on_a_stale_file(monkeypatch, tmp_path):
    """A pre-Phase-5 ``users`` row must survive the upgrade intact.

    Pairs with ``test_init_db_upgrades_a_stale_database_to_head``: the
    user reported the column was missing, but the rows that *were*
    there (email, display_name, created_at) are valuable data and
    must not be lost when the schema is brought up to ``head``.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    alembic_config = Config(ROOT / "alembic.ini")
    alembic_config.set_main_option(
        "script_location", str(ROOT / "src" / "sra" / "alembic")
    )
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(alembic_config, INITIAL_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, created_at) "
                "VALUES (42, 'legacy@example.com', 'Legacy User', "
                "'2026-08-01 12:00:00')"
            )
        )
    engine.dispose()

    init_db()

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT email, display_name, preferred_review_time, timezone "
                "FROM users WHERE id = 42"
            )
        ).one()
    engine.dispose()
    # Legacy row keeps its identity; new columns get the documented defaults.
    assert row == ("legacy@example.com", "Legacy User", "09:00", "UTC")


def test_init_db_repairs_a_pre_alembic_database(monkeypatch, tmp_path):
    """A SQLite file with tables but no ``alembic_version`` row is repaired.

    The original failure mode: a developer had a local ``sra.db`` that
    was created by the Phase 1 ``SQLModel.metadata.create_all`` call,
    before Alembic existed. The file has the four-column ``users`` table
    from the initial model, but no ``alembic_version`` table at all.
    ``alembic upgrade head`` alone cannot recover -- it does not know
    where to start, and re-running the initial migration would fail
    with ``table users already exists``.

    ``init_db()`` must detect the missing ``alembic_version`` table and
    stamp the database at the initial revision first, so the subsequent
    ``upgrade head`` walks forward and lands every later column, table,
    index, and CHECK constraint. This is the exact shape of the
    ``sra.db`` that produced the original
    ``no such column: users.preferred_review_time`` crash.

    We seed the pre-Alembic file with raw SQL that mirrors the initial
    migration, not with ``SQLModel.metadata.create_all`` -- the latter
    would happily use the current model (with every Phase 5 column)
    and therefore would not exercise the regression.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    legacy_engine = create_engine(f"sqlite:///{database}")
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users ("
                "    email VARCHAR NOT NULL,"
                "    display_name VARCHAR NOT NULL,"
                "    id INTEGER NOT NULL,"
                "    created_at DATETIME NOT NULL,"
                "    PRIMARY KEY (id),"
                "    UNIQUE (email)"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE study_entries ("
                "    id INTEGER NOT NULL,"
                "    topic VARCHAR NOT NULL,"
                "    subtopic VARCHAR,"
                "    tags JSON,"
                "    summary VARCHAR NOT NULL,"
                "    raw_transcript VARCHAR NOT NULL,"
                "    user_id INTEGER NOT NULL,"
                "    created_at DATETIME NOT NULL,"
                "    PRIMARY KEY (id),"
                "    FOREIGN KEY(user_id) REFERENCES users (id)"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, created_at) "
                "VALUES (1, 'pre-alembic@example.com', 'Pre-Alembic', "
                "'2026-08-01 12:00:00')"
            )
        )
    legacy_engine.dispose()

    # Sanity: the pre-Alembic file really is missing the version table
    # and really is missing the Phase 5 column.
    legacy_engine = create_engine(f"sqlite:///{database}")
    legacy_tables = set(inspect(legacy_engine).get_table_names())
    legacy_engine.dispose()
    assert "alembic_version" not in legacy_tables, (
        "test precondition: the pre-Alembic file must not already have "
        "an alembic_version table, otherwise the regression would not "
        "be exercised"
    )
    assert "preferred_review_time" not in _user_columns(database), (
        "test precondition: the pre-Alembic schema must not already "
        "contain the Phase 5 column, otherwise the regression would not "
        "be exercised"
    )

    init_db()

    assert USER_MODEL_COLUMNS <= _user_columns(database), (
        "init_db() must stamp a pre-Alembic database at the base "
        "revision and then upgrade to head, adding every User model "
        "column so the original OperationalError never recurs"
    )
    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT email, preferred_review_time, timezone "
                "FROM users WHERE id = 1"
            )
        ).one()
    engine.dispose()
    assert row == ("pre-alembic@example.com", "09:00", "UTC"), (
        "the pre-Alembic user row must survive the repair with the "
        "documented defaults applied to the newly-added columns"
    )


def test_init_db_raises_clear_error_on_inconsistent_state(monkeypatch, tmp_path):
    """A database whose ``alembic_version`` does not match its schema
    objects must surface an actionable error, not a raw SQLite
    ``OperationalError: table X already exists``.

    This is the exact shape of the ``sra.db`` that produced the
    user's second crash: ``alembic_version`` recorded a revision
    that did not match the tables on disk, so ``upgrade head`` tried
    to create objects that were already there. The previous
    implementation bubbled the raw SQLAlchemy error up to
    ``create_app()``; the fix wraps the upgrade and re-raises with a
    message that names the recovery path (``reset_db()`` or deleting
    the file) so the developer is not left staring at
    ``table quiz_sets already exists``.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    alembic_config = Config(ROOT / "alembic.ini")
    alembic_config.set_main_option(
        "script_location", str(ROOT / "src" / "sra" / "alembic")
    )
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    # Bring the file to the OAuth revision, then write a Phase 4
    # table by hand without bumping alembic_version. The result is a
    # file where ``alembic_version`` says "OAuth applied" but the
    # schema has Phase 4 objects that the chain has never heard of.
    command.upgrade(alembic_config, "8cb1b420d9d2")
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE quiz_sets ("
                "    id INTEGER NOT NULL,"
                "    entry_id INTEGER NOT NULL,"
                "    user_id INTEGER NOT NULL,"
                "    generated_on DATE NOT NULL,"
                "    question_count INTEGER NOT NULL,"
                "    option_count INTEGER NOT NULL,"
                "    PRIMARY KEY (id)"
                ")"
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError) as excinfo:
        init_db()

    message = str(excinfo.value)
    assert "inconsistent state" in message
    assert "reset_db" in message, (
        "the recovery path (reset_db or delete the file) must be "
        "named in the error so the developer is not left guessing"
    )


def test_reset_db_repairs_an_inconsistent_database(monkeypatch, tmp_path):
    """``reset_db()`` drops every table (including orphans the
    migration chain never knew about) and re-runs the chain from
    scratch, so a database in the inconsistent state from the
    previous test is back at ``head`` and a subsequent ``init_db``
    is a no-op.
    """
    database = _tmp_database(tmp_path)
    _point_settings_at(monkeypatch, database)

    alembic_config = Config(ROOT / "alembic.ini")
    alembic_config.set_main_option(
        "script_location", str(ROOT / "src" / "sra" / "alembic")
    )
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(alembic_config, "8cb1b420d9d2")
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE quiz_sets ("
                "    id INTEGER NOT NULL,"
                "    entry_id INTEGER NOT NULL,"
                "    user_id INTEGER NOT NULL,"
                "    generated_on DATE NOT NULL,"
                "    question_count INTEGER NOT NULL,"
                "    option_count INTEGER NOT NULL,"
                "    PRIMARY KEY (id)"
                ")"
            )
        )
        # And a pre-existing user row the developer would not want
        # to recreate by hand.
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, role, created_at) "
                "VALUES (99, 'reset@example.com', 'Reset', 'user', "
                "'2026-09-01 12:00:00')"
            )
        )
    engine.dispose()

    reset_db()

    assert USER_MODEL_COLUMNS <= _user_columns(database)
    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        rows = list(connection.execute(text("SELECT email FROM users")))
        version = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    engine.dispose()
    assert rows == [], "reset_db drops every row in every table"
    assert version == "d4e5f6a7b8c9", (
        "reset_db must leave the database at the current head revision"
    )

    # A subsequent init_db is a no-op -- the schema is already at head.
    init_db()
    assert USER_MODEL_COLUMNS <= _user_columns(database)
