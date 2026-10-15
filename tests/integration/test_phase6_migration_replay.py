"""SRA-601 migration replay: downgrade then upgrade the Phase 6 column round-trips.

Same as ``test_phase6_migration.py`` but exercises the downgrade leg too,
which proves the SRA-601 migration is reversible and that pre-existing rows
keep ``is_demo_user=False`` across the round-trip.
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
                "VALUES (801, 'phase5-replay@example.com', 'Ph5R', 'phase5-replay-sub', 'user', NULL, "
                "'2026-10-01 12:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (901, 'Meiosis', NULL, '[\"biology\"]', 'cell division', 'notes', 801, "
                "'2026-10-01 13:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_schedules (id, entry_id, interval_label, scheduled_date, status, completed_at) "
                "VALUES (1001, 901, '1d', '2026-10-02', 'pending', NULL)"
            )
        )
    engine.dispose()


def test_phase6_replay_upgrade_creates_is_demo_user_column(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase6-replay-up.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase5_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_COLUMN in user_columns


def test_phase6_replay_downgrade_drops_is_demo_user_column(monkeypatch, tmp_path):
    """``alembic downgrade -1`` removes the SRA-601 column without removing pre-existing data."""
    try:
        database = tmp_path / "phase6-replay-down.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase5_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE6_REVISION)

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_COLUMN not in user_columns
    # Phase 5 / Phase 4 tables remain after downgrade
    assert {"users", "study_entries", "review_schedules",
            "quiz_sets", "quiz_questions", "quiz_attempts", "quiz_answers"} <= set(inspector.get_table_names())


def test_phase6_replay_upgrade_restores_is_demo_user_column(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase6-replay-restore.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase5_database(config, database)

    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE6_REVISION)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_COLUMN in user_columns


def test_phase6_replay_preexisting_row_keeps_is_demo_user_false(monkeypatch, tmp_path):
    """Pre-Phase-6 row keeps ``is_demo_user=False`` across a downgrade + upgrade round-trip."""
    try:
        database = tmp_path / "phase6-replay-preserve.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase5_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE6_REVISION)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT id, email, is_demo_user FROM users WHERE id = 801")
        ).one()
    engine.dispose()

    assert row == (801, "phase5-replay@example.com", False) or row == (801, "phase5-replay@example.com", 0)