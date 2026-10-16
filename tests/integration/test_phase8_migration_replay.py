
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-809 — Migration round-trip: upgrade/downgrade/upgrade preserves pre-Phase-8 rows."""

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text

from sra.config import get_settings

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE8_REVISION = "d4e5f6a7b8c9"
EXPECTED_NEW_TABLES = {"interview_plans", "interview_turns"}
EXPECTED_NEW_USER_COLUMNS = {"interview_question_count", "interview_target_minutes"}
EXPECTED_NEW_ATTEMPT_COLUMNS = {"cursor_on_plan"}


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


def _seed_phase6_database(config: Config, database: Path) -> None:
    command.upgrade(config, PRE_PHASE8_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", _register_sqlite_regexp)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, google_sub, role, last_login_at, created_at, "
                "preferred_review_time, timezone, quiz_question_count, quiz_option_count, "
                "wakeup_notification_sent_at, is_demo_user) "
                "VALUES (1001, 'phase6-replay@example.com', 'Ph6R', 'phase6-replay-sub', 'user', NULL, "
                "'2026-09-01 12:00:00', '09:00', 'UTC', 10, 4, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries (id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (1101, 'Mitosis', NULL, '[\"biology\"]', 'cell division', 'notes', 1001, "
                "'2026-09-01 13:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_schedules (id, entry_id, interval_label, scheduled_date, status, completed_at) "
                "VALUES (1201, 1101, '1d', '2026-09-02', 'pending', NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_sets (id, entry_id, user_id, generated_on, question_count, option_count) "
                "VALUES (1301, 1101, 1001, '2026-10-14', 2, 4)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_attempts (id, quiz_set_id, user_id, mode, total_questions, started_at, completed_at, score, cursor) "
                "VALUES (1401, 1301, 1001, 'web', 2, '2026-10-14 09:00:00', NULL, NULL, 0)"
            )
        )
    engine.dispose()


def test_phase8_replay_upgrade_creates_two_interview_tables(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-replay-up.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES <= tables


def test_phase8_replay_downgrade_drops_two_interview_tables(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-replay-down.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE8_REVISION)

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    attempt_columns = {column["name"] for column in inspector.get_columns("quiz_attempts")}
    assert EXPECTED_NEW_TABLES.isdisjoint(tables)
    assert EXPECTED_NEW_USER_COLUMNS.isdisjoint(user_columns)
    assert EXPECTED_NEW_ATTEMPT_COLUMNS.isdisjoint(attempt_columns)
    assert {"users", "study_entries", "review_schedules",
            "quiz_sets", "quiz_questions", "quiz_attempts", "quiz_answers"} <= tables


def test_phase8_replay_upgrade_after_downgrade_restores_new_objects(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-replay-restore.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE8_REVISION)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    attempt_columns = {column["name"] for column in inspector.get_columns("quiz_attempts")}
    assert EXPECTED_NEW_TABLES <= tables
    assert EXPECTED_NEW_USER_COLUMNS <= user_columns
    assert EXPECTED_NEW_ATTEMPT_COLUMNS <= attempt_columns


def test_phase8_replay_preexisting_rows_intact_after_round_trip(monkeypatch, tmp_path):
    """Pre-Phase-8 user, entry, review_schedule, quiz_set, and quiz_attempt must survive."""

    try:
        database = tmp_path / "phase8-replay-preserve.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE8_REVISION)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        user_row = connection.execute(
            text("SELECT id, email, is_demo_user FROM users WHERE id = 1001")
        ).one()
        entry_row = connection.execute(
            text("SELECT id, user_id, topic, created_at FROM study_entries WHERE id = 1101")
        ).one()
        review_row = connection.execute(
            text("SELECT count(*) FROM review_schedules WHERE entry_id = 1101")
        ).scalar_one()
        quiz_set_row = connection.execute(
            text(
                "SELECT id, entry_id, user_id, generated_on, question_count, option_count "
                "FROM quiz_sets WHERE id = 1301"
            )
        ).one()
        attempt_row = connection.execute(
            text(
                "SELECT id, quiz_set_id, user_id, mode, total_questions, started_at, cursor "
                "FROM quiz_attempts WHERE id = 1401"
            )
        ).one()
    engine.dispose()

    assert user_row[0] == 1001
    assert entry_row[0] == 1101
    assert review_row >= 1
    assert quiz_set_row[0] == 1301
    assert attempt_row[0] == 1401
    assert attempt_row[6] == 0


def test_phase8_replay_preexisting_user_keeps_preference_defaults(monkeypatch, tmp_path):
    """Pre-Phase-8 rows gain the two new preference defaults without losing any prior data."""

    try:
        database = tmp_path / "phase8-replay-defaults.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE8_REVISION)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT interview_question_count, interview_target_minutes, cursor_on_plan "
                "FROM users JOIN quiz_attempts ON quiz_attempts.user_id = users.id "
                "WHERE users.id = 1001"
            )
        ).first()
        cursor_row = connection.execute(
            text("SELECT cursor_on_plan FROM quiz_attempts WHERE id = 1401")
        ).one()
    engine.dispose()

    assert row is not None
    assert row[0] == 6
    assert row[1] == 5
    assert cursor_row[0] == 0
