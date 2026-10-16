"""SRA-801 — Alembic migration rehearsal for the two interview tables and three columns.

A temporary file-based SQLite database is seeded with one Phase 5/6 user, one entry,
one ``QuizSet``, one ``QuizAttempt`` (mode="web"), and one ``QuizAnswer``, then
``alembic upgrade head`` is run. The new tables and columns must exist, every
pre-existing row must keep its IDs and timestamps, and a second
``InterviewPlan`` with the same ``quiz_set_id`` must violate the unique
constraint. Downgrade + upgrade restores the original shape.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from sra.config import get_settings

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE8_REVISION = "d4e5f6a7b8c9"  # chains off the Phase 6 is_demo_user revision
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
    """Seed the file DB at the Phase 6 revision with representative rows."""
    command.upgrade(config, PRE_PHASE8_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", _register_sqlite_regexp)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, display_name, google_sub, role, last_login_at, created_at, "
                " preferred_review_time, timezone, quiz_question_count, quiz_option_count, "
                " wakeup_notification_sent_at, is_demo_user) "
                "VALUES (901, 'phase6@example.com', 'Ph6', 'phase6-sub', 'user', NULL, "
                "'2026-09-01 12:00:00', '09:00', 'UTC', 10, 4, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (1001, 'Mitosis', NULL, '[\"biology\"]', 'cell division', 'notes', "
                "901, '2026-09-01 13:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_schedules (id, entry_id, interval_label, scheduled_date, status, completed_at) "
                "VALUES (1101, 1001, '1d', '2026-09-02', 'pending', NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_sets "
                "(id, entry_id, user_id, generated_on, question_count, option_count) "
                "VALUES (1201, 1001, 901, '2026-10-14', 2, 4)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_questions "
                "(id, quiz_set_id, question_text, options, correct_option_index, explanation, order_index) "
                "VALUES (1301, 1201, 'q0', '[\"a\",\"b\",\"c\",\"d\"]', 0, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_attempts "
                "(id, quiz_set_id, user_id, mode, total_questions, started_at, completed_at, score, cursor) "
                "VALUES (1401, 1201, 901, 'web', 2, '2026-10-14 09:00:00', NULL, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_answers "
                "(id, attempt_id, question_id, selected_option_index, is_correct, answered_at) "
                "VALUES (1501, 1401, 1301, 0, 1, '2026-10-14 09:00:30')"
            )
        )
    engine.dispose()


def test_phase8_migration_creates_two_new_tables(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-tables.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES <= tables, f"missing tables: {EXPECTED_NEW_TABLES - tables}"


def test_phase8_migration_adds_the_two_user_columns(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-usercols.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert EXPECTED_NEW_USER_COLUMNS <= user_columns


def test_phase8_migration_adds_quiz_attempt_cursor_on_plan_column(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-attemptcol.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    attempt_columns = {column["name"] for column in inspector.get_columns("quiz_attempts")}
    assert EXPECTED_NEW_ATTEMPT_COLUMNS <= attempt_columns


def test_phase8_migration_preserves_phase6_row_ids_and_timestamps(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-preserve.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        user_row = connection.execute(
            text("SELECT id, email, google_sub, created_at FROM users WHERE id = 901")
        ).one()
        entry_row = connection.execute(
            text("SELECT id, user_id, topic, created_at FROM study_entries WHERE id = 1001")
        ).one()
        attempt_row = connection.execute(
            text(
                "SELECT id, quiz_set_id, user_id, mode, total_questions, started_at, cursor "
                "FROM quiz_attempts WHERE id = 1401"
            )
        ).one()
    engine.dispose()

    assert user_row == (901, "phase6@example.com", "phase6-sub", "2026-09-01 12:00:00")
    assert entry_row == (1001, 901, "Mitosis", "2026-09-01 13:00:00")
    assert attempt_row[0] == 1401
    assert attempt_row[1] == 1201
    assert attempt_row[2] == 901
    assert attempt_row[3] == "web"
    assert attempt_row[4] == 2
    assert attempt_row[5] == "2026-10-14 09:00:00"
    # Pre-existing Phase 5 attempts read cursor_on_plan as 0 (server default)
    assert attempt_row[6] == 0


def test_phase8_migration_unique_constraint_rejects_duplicate_interview_plan(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-unique-plan.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase6_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO interview_plans (quiz_set_id, outline, target_questions, created_at) "
                "VALUES (1201, '[{\"subtopic\":\"a\",\"difficulty\":\"easy\",\"focus_prompt\":\"b\"}]', "
                "1, '2026-10-14 10:00:00')"
            )
        )

    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO interview_plans (quiz_set_id, outline, target_questions, created_at) "
                "VALUES (1201, '[{\"subtopic\":\"x\",\"difficulty\":\"hard\",\"focus_prompt\":\"y\"}]', "
                "1, '2026-10-14 10:01:00')"
            )
        )
    engine.dispose()


def test_phase8_migration_downgrade_removes_only_new_objects(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-downgrade.db"
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
    # Phase 1-6 tables remain
    assert {
        "users",
        "study_entries",
        "review_schedules",
        "quiz_sets",
        "quiz_questions",
        "quiz_attempts",
        "quiz_answers",
    } <= tables


def test_phase8_migration_upgrade_after_downgrade_restores_new_objects(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase8-restore.db"
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
