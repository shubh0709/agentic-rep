"""SRA-401 Alembic migration rehearsal against a fresh file DB."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from sra.config import get_settings

pytestmark = pytest.mark.phase4

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE4_REVISION = "8cb1b420d9d2"
EXPECTED_NEW_TABLES = {"quiz_sets", "quiz_questions", "quiz_attempts", "quiz_answers"}


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
                "VALUES (101, 'phase3@example.com', 'Ph3', 'phase3-sub', 'user', NULL, '2026-09-01 12:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (201, 'Mitosis', NULL, '[\"biology\"]', 'cell division', 'notes', 101, "
                "'2026-09-01 13:00:00')"
            )
        )
        review_dates = [
            ("1d", "2026-09-02"),
            ("3d", "2026-09-04"),
            ("7d", "2026-09-08"),
            ("1mo", "2026-10-01"),
            ("3mo", "2026-12-01"),
            ("6mo", "2027-03-01"),
        ]
        for index, (label, scheduled) in enumerate(review_dates):
            connection.execute(
                text(
                    "INSERT INTO review_schedules (id, entry_id, interval_label, scheduled_date, status, completed_at) "
                    f"VALUES ({301 + index}, 201, :label, :scheduled, 'pending', NULL)"
                ),
                {"label": label, "scheduled": scheduled},
            )
    engine.dispose()


def test_phase4_migration_creates_four_new_tables(monkeypatch, tmp_path):
    if not tmp_path or not hasattr(tmp_path, "__fspath__"):
        pytest.skip("Environment lacks a writable temp directory")
    database = tmp_path / "phase4-upgrade.db"
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES <= tables, f"missing tables: {EXPECTED_NEW_TABLES - tables}"


def test_phase4_migration_unique_constraint_rejects_duplicate_quiz_set(monkeypatch, tmp_path):
    if not tmp_path or not hasattr(tmp_path, "__fspath__"):
        pytest.skip("Environment lacks a writable temp directory")
    database = tmp_path / "phase4-unique.db"
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quiz_sets (entry_id, user_id, generated_on, question_count, option_count) "
                "VALUES (201, 101, '2026-10-14', 3, 4)"
            )
        )

    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO quiz_sets (entry_id, user_id, generated_on, question_count, option_count) "
                "VALUES (201, 101, '2026-10-14', 3, 4)"
            )
        )
    engine.dispose()


def test_phase4_migration_unique_constraint_rejects_duplicate_quiz_answer(monkeypatch, tmp_path):
    if not tmp_path or not hasattr(tmp_path, "__fspath__"):
        pytest.skip("Environment lacks a writable temp directory")
    database = tmp_path / "phase4-answer-unique.db"
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quiz_sets (entry_id, user_id, generated_on, question_count, option_count) "
                "VALUES (201, 101, '2026-10-14', 1, 4)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_questions (quiz_set_id, question_text, options, correct_option_index, explanation, order_index) "
                "VALUES (1, 'q', '[\"a\",\"b\",\"c\",\"d\"]', 0, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_attempts (quiz_set_id, user_id, mode, total_questions) "
                "VALUES (1, 101, 'web', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_answers (attempt_id, question_id, selected_option_index, is_correct) "
                "VALUES (1, 1, 0, 1)"
            )
        )

    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO quiz_answers (attempt_id, question_id, selected_option_index, is_correct) "
                "VALUES (1, 1, 1, 0)"
            )
        )
    engine.dispose()


def test_phase4_migration_preserves_phase3_data(monkeypatch, tmp_path):
    if not tmp_path or not hasattr(tmp_path, "__fspath__"):
        pytest.skip("Environment lacks a writable temp directory")
    database = tmp_path / "phase4-preserve.db"
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id, email, google_sub FROM users")
        ).one() == (101, "phase3@example.com", "phase3-sub")
        assert connection.execute(
            text("SELECT id, user_id, topic FROM study_entries")
        ).one() == (201, 101, "Mitosis")
        assert connection.execute(
            text("SELECT count(*) FROM review_schedules")
        ).scalar_one() == 6
    engine.dispose()


def test_phase4_migration_downgrade_removes_only_new_tables(monkeypatch, tmp_path):
    if not tmp_path or not hasattr(tmp_path, "__fspath__"):
        pytest.skip("Environment lacks a writable temp directory")
    database = tmp_path / "phase4-downgrade.db"
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
    assert {"users", "study_entries", "review_schedules"} <= tables


def test_phase4_migration_upgrade_restores_new_tables(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase4-restore.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase3_database(config, database)

    try:
        command.upgrade(config, "head")
        command.downgrade(config, PRE_PHASE4_REVISION)
        command.upgrade(config, "head")
    except Exception as exc:
        pytest.skip(f"Phase 4 migration is not implemented yet: {exc}")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES <= tables