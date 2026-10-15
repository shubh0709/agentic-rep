"""SRA-601 Alembic migration rehearsal: the new ``is_demo_user`` column lands and defaults populate cleanly.

The chain starts at ``c3d4e5f6a7b8`` (Phase 5 quiz attempt cursor); this test
does not pin the SRA-601 revision id -- the implementation agent chooses one.
We seed through the pre-Phase-6 head, then ``alembic upgrade head`` to land
every Phase 6 revision, then assert the documented shape.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from sra.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE6_REVISION = "c3d4e5f6a7b8"
EXPECTED_NEW_COLUMN = "is_demo_user"


def _alembic_config(monkeypatch: pytest.MonkeyPatch, database: Path) -> Config:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-migration-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    return Config(ROOT / "alembic.ini")


def _seed_phase5_database(config: Config, database: Path) -> None:
    command.upgrade(config, PRE_PHASE6_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, google_sub, role, last_login_at, created_at) "
                "VALUES (701, 'phase5@example.com', 'Ph5', 'phase5-sub', 'user', NULL, '2026-10-01 12:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (801, 'Mitosis', NULL, '[\"biology\"]', 'cell division', 'notes', 701, "
                "'2026-10-01 13:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_schedules (id, entry_id, interval_label, scheduled_date, status, completed_at) "
                "VALUES (901, 801, '1d', '2026-10-02', 'pending', NULL)"
            )
        )
    engine.dispose()


def test_phase6_migration_creates_is_demo_user_column(monkeypatch, tmp_path):
    """``alembic upgrade head`` from the Phase 5 head lands the ``is_demo_user`` column."""
    try:
        database = tmp_path / "phase6-upgrade.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase5_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_COLUMN in user_columns


def test_phase6_migration_preexisting_rows_default_to_false(monkeypatch, tmp_path):
    """Pre-Phase-6 rows keep ``is_demo_user=False`` thanks to ``server_default=false``."""
    try:
        database = tmp_path / "phase6-defaults.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase5_database(config, database)

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT is_demo_user FROM users WHERE id = 701")
        ).scalar_one()
    engine.dispose()

    assert value in (False, 0)


def test_phase6_migration_is_demo_user_accepts_true(monkeypatch, tmp_path):
    """The new column accepts an explicit True value once the migration has landed."""
    try:
        database = tmp_path / "phase6-accepts-true.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase5_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, google_sub, role, is_demo_user, created_at) "
                "VALUES (702, 'demo@example.com', 'Demo', 'demo-sub', 'user', 1, '2026-10-02 12:00:00')"
            )
        )
        flagged = connection.execute(
            text("SELECT is_demo_user FROM users WHERE id = 702")
        ).scalar_one()
    engine.dispose()

    assert flagged in (True, 1)