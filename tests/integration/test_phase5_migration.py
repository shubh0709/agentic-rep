"""SRA-501 Alembic migration rehearsal against a fresh file DB."""

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from sra.config import get_settings

pytestmark = pytest.mark.phase5

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE5_REVISION = "a1b2c3d4e5f6"
EXPECTED_NEW_COLUMNS = {
    "preferred_review_time",
    "timezone",
    "quiz_question_count",
    "quiz_option_count",
    "wakeup_notification_sent_at",
}
EXPECTED_NEW_CHECK_CONSTRAINTS = {
    "ck_users_preferred_review_time_shape",
    "ck_users_quiz_counts_positive",
}


def _alembic_config(monkeypatch: pytest.MonkeyPatch, database: Path) -> Config:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-migration-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    return Config(ROOT / "alembic.ini")


def _register_sqlite_regexp(dbapi_connection, _connection_record):  # noqa: ANN001
    dbapi_connection.create_function(
        "regexp",
        2,
        lambda pattern, value: int(
            value is not None and re.fullmatch(pattern, value) is not None
        ),
    )


def _seed_phase4_database(config: Config, database: Path) -> None:
    command.upgrade(config, PRE_PHASE5_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", _register_sqlite_regexp)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, google_sub, role, last_login_at, created_at) "
                "VALUES (301, 'phase4@example.com', 'Ph4', 'phase4-sub', 'user', NULL, '2026-09-01 12:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (401, 'Mitosis', NULL, '[\"biology\"]', 'cell division', 'notes', 301, "
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
                    f"VALUES ({501 + index}, 401, :label, :scheduled, 'pending', NULL)"
                ),
                {"label": label, "scheduled": scheduled},
            )
    engine.dispose()


def test_phase5_migration_creates_five_new_user_columns(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-upgrade.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_COLUMNS <= user_columns, (
        f"missing user columns: {EXPECTED_NEW_COLUMNS - user_columns}"
    )


def test_phase5_migration_applies_documented_defaults(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-defaults.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT preferred_review_time, timezone, quiz_question_count, "
                "quiz_option_count, wakeup_notification_sent_at FROM users WHERE id = 301"
            )
        ).one()
    engine.dispose()

    assert row == ("09:00", "UTC", 10, 4, None)


def test_phase5_migration_installs_documented_check_constraints(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-checks.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    found = {ck.get("name") for ck in inspector.get_check_constraints("users")}
    missing = EXPECTED_NEW_CHECK_CONSTRAINTS - found
    assert not missing, f"missing check constraints: {missing}"


def test_phase5_migration_check_constraint_rejects_invalid_hhmm(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-check-hhmm.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", _register_sqlite_regexp)
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE users SET preferred_review_time = '9:00' WHERE id = 301")
        )
    engine.dispose()


def test_phase5_migration_check_constraint_rejects_out_of_range_counts(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-check-counts.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", _register_sqlite_regexp)
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE users SET quiz_question_count = 99 WHERE id = 301")
        )
    engine.dispose()


def test_phase5_migration_preserves_phase4_data(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-preserve.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id, email, google_sub FROM users WHERE id = 301")
        ).one() == (301, "phase4@example.com", "phase4-sub")
        assert connection.execute(
            text("SELECT id, user_id, topic FROM study_entries WHERE id = 401")
        ).one() == (401, 301, "Mitosis")
        assert connection.execute(
            text("SELECT count(*) FROM review_schedules WHERE entry_id = 401")
        ).scalar_one() == 6
    engine.dispose()


def test_phase5_migration_downgrade_removes_only_new_columns(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-downgrade.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)
    command.upgrade(config, "head")

    command.downgrade(config, PRE_PHASE5_REVISION)

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_COLUMNS.isdisjoint(user_columns)
    assert {"users", "study_entries", "review_schedules"} <= set(inspector.get_table_names())
    # Phase 4 tables remain after downgrade
    assert {
        "quiz_sets",
        "quiz_questions",
        "quiz_attempts",
        "quiz_answers",
    } <= set(inspector.get_table_names())


def test_phase5_migration_upgrade_restores_new_columns(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase5-restore.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase4_database(config, database)

    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE5_REVISION)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_COLUMNS <= user_columns