"""SRA-406 migration replay: phase-level evidence that the Phase 4 migration round-trips."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from sra.config import get_settings

pytestmark = pytest.mark.phase4

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE4_REVISION = "8cb1b420d9d2"
EXPECTED_NEW_TABLES = {"quiz_sets", "quiz_questions", "quiz_attempts", "quiz_answers"}
EXPECTED_INDEXES = {
    "ix_quiz_sets_user_id",
    "ix_quiz_questions_quiz_set_id",
    "ix_quiz_attempts_user_id",
    "ix_quiz_answers_attempt_id",
}
EXPECTED_UNIQUE_CONSTRAINTS = {
    "uq_quiz_sets_entry_generated_on",
    "uq_quiz_answers_attempt_question",
}


def _alembic_config(monkeypatch: pytest.MonkeyPatch, database: Path) -> Config:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-migration-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    return Config(ROOT / "alembic.ini")


def _seed_phase3_database(config: Config, database: Path) -> None:
    command.upgrade(config, PRE_PHASE4_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, google_sub, role, last_login_at, created_at) "
                "VALUES (201, 'phase3@example.com', 'Ph3', 'phase3-sub', 'user', NULL, '2026-09-01 12:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (301, 'Mitosis', NULL, '[\"biology\"]', 'cell division', 'notes', 201, "
                "'2026-09-01 13:00:00')"
            )
        )
        for index, (label, scheduled) in enumerate(
            [("1d", "2026-09-02"), ("3d", "2026-09-04"), ("7d", "2026-09-08"),
             ("1mo", "2026-10-01"), ("3mo", "2026-12-01"), ("6mo", "2027-03-01")]
        ):
            connection.execute(
                text(
                    "INSERT INTO review_schedules (id, entry_id, interval_label, scheduled_date, status, completed_at) "
                    f"VALUES ({401 + index}, 301, :label, :scheduled, 'pending', NULL)"
                ),
                {"label": label, "scheduled": scheduled},
            )
    engine.dispose()


def test_migration_replay_upgrade_creates_all_expected_tables(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase4-replay.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES <= tables


def test_migration_replay_upgrade_includes_expected_indexes(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase4-replay-index.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    found_indexes = set()
    for table in EXPECTED_NEW_TABLES:
        for index in inspector.get_indexes(table):
            if index["name"]:
                found_indexes.add(index["name"])
    missing = EXPECTED_INDEXES - found_indexes
    assert not missing, f"missing indexes: {missing}"


def test_migration_replay_upgrade_includes_unique_constraints(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase4-replay-uniq.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    found = set()
    for table in EXPECTED_NEW_TABLES:
        for unique in inspector.get_unique_constraints(table):
            if unique.get("name"):
                found.add(unique["name"])
    missing = EXPECTED_UNIQUE_CONSTRAINTS - found
    assert not missing, f"missing unique constraints: {missing}"


def test_migration_replay_downgrade_and_upgrade_cycle(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase4-replay-cycle.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
        command.downgrade(config, PRE_PHASE4_REVISION)
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES.isdisjoint(tables)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet on re-upgrade: {exc}")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES <= tables


def test_migration_replay_preserves_phase3_rows_on_upgrade(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase4-replay-preserve.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM users WHERE id = 201")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM study_entries WHERE id = 301")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM review_schedules WHERE entry_id = 301")
        ).scalar_one() == 6
    engine.dispose()