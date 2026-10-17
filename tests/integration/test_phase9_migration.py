
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — Alembic migration rehearsal for the ``reference_materials`` table.

Mirrors the style of ``tests/integration/test_phase8_migration.py`` and
``tests/integration/test_phase8_migration_replay.py``:

  1. Seed a temporary file-based SQLite DB at the ``e5f6a7b8c9d0``
     (Phase 8 interview) revision with a user, an entry, a review
     schedule, a quiz set, a quiz attempt, and a quiz answer.
  2. Run ``alembic upgrade head`` to apply the Phase 9 reference
     materials migration.
  3. Assert the ``reference_materials`` table exists and the CHECK
     constraint is present.
  4. ``alembic downgrade -1`` and confirm the table is gone but
     pre-Phase-9 rows survive.
  5. ``alembic upgrade head`` again; pre-Phase-9 rows still survive.

The Phase 9 migration file is owned by SRA-901 step 3; this test is
expected to fail today because the migration does not exist yet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

from sra.config import get_settings

pytestmark = pytest.mark.phase9

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE9_REVISION = "e5f6a7b8c9d0"  # chains off the Phase 8 interview revision
EXPECTED_NEW_TABLES = {"reference_materials"}
EXPECTED_CHECK_CONSTRAINT = "ck_reference_materials_kind"
EXPECTED_INDEX = "ix_reference_materials_entry_id"


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


def _seed_phase8_database(config: Config, database: Path) -> None:
    """Seed the file DB at the Phase 8 revision with representative rows."""
    command.upgrade(config, PRE_PHASE9_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    event.listen(engine, "connect", _register_sqlite_regexp)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, display_name, google_sub, role, last_login_at, created_at, "
                " preferred_review_time, timezone, quiz_question_count, quiz_option_count, "
                " interview_question_count, interview_target_minutes, wakeup_notification_sent_at, is_demo_user) "
                "VALUES (1901, 'phase8-p9@example.com', 'Ph8P9', 'phase8-p9-sub', 'user', NULL, "
                "'2026-09-01 12:00:00', '09:00', 'UTC', 10, 4, 6, 5, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (2001, 'Topic9', NULL, '[\"phase9\"]', 'summary', 'transcript', "
                "1901, '2026-09-01 13:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_schedules (id, entry_id, interval_label, scheduled_date, status, completed_at) "
                "VALUES (2101, 2001, '1d', '2026-09-02', 'pending', NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_sets "
                "(id, entry_id, user_id, generated_on, question_count, option_count) "
                "VALUES (2201, 2001, 1901, '2026-10-15', 2, 4)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_questions "
                "(id, quiz_set_id, question_text, options, correct_option_index, explanation, order_index) "
                "VALUES (2301, 2201, 'q0', '[\"a\",\"b\",\"c\",\"d\"]', 0, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_attempts "
                "(id, quiz_set_id, user_id, mode, total_questions, started_at, completed_at, score, cursor, cursor_on_plan) "
                "VALUES (2401, 2201, 1901, 'web', 2, '2026-10-15 09:00:00', NULL, NULL, 0, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_answers "
                "(id, attempt_id, question_id, selected_option_index, is_correct, answered_at) "
                "VALUES (2501, 2401, 2301, 0, 1, '2026-10-15 09:00:30')"
            )
        )
    engine.dispose()


# ---------------------------------------------------------------------------
# Forward migration
# ---------------------------------------------------------------------------


def test_phase9_migration_creates_reference_materials_table(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase9-table.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES <= tables, f"missing tables: {EXPECTED_NEW_TABLES - tables}"


def test_phase9_migration_creates_the_check_constraint(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase9-ck.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    check_constraints = {
        c["name"] for c in inspector.get_check_constraints("reference_materials")
    }
    assert EXPECTED_CHECK_CONSTRAINT in check_constraints


def test_phase9_migration_creates_entry_id_index(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase9-idx.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    indexes = {idx["name"] for idx in inspector.get_indexes("reference_materials")}
    assert EXPECTED_INDEX in indexes


def test_phase9_migration_check_constraint_rejects_url_kind_without_url(
    monkeypatch, tmp_path
):
    """The CHECK constraint must reject ``kind='url'`` rows with NULL url."""
    try:
        database = tmp_path / "phase9-ck-url.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO reference_materials "
                "(entry_id, kind, title, url, pasted_text, file_name, content_type, "
                " extracted_text, fetch_status, fetch_error, fetched_at, created_at) "
                "VALUES (2001, 'url', 'broken', NULL, NULL, NULL, NULL, "
                "NULL, 'pending', NULL, NULL, '2026-10-16 09:00:00')"
            )
        )
    engine.dispose()


def test_phase9_migration_check_constraint_rejects_paste_kind_without_pasted_text(
    monkeypatch, tmp_path
):
    try:
        database = tmp_path / "phase9-ck-paste.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO reference_materials "
                "(entry_id, kind, title, url, pasted_text, file_name, content_type, "
                " extracted_text, fetch_status, fetch_error, fetched_at, created_at) "
                "VALUES (2001, 'paste', 'broken', NULL, NULL, NULL, NULL, "
                "NULL, 'pending', NULL, NULL, '2026-10-16 09:00:00')"
            )
        )
    engine.dispose()


def test_phase9_migration_check_constraint_rejects_upload_kind_without_file_metadata(
    monkeypatch, tmp_path
):
    try:
        database = tmp_path / "phase9-ck-upload.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO reference_materials "
                "(entry_id, kind, title, url, pasted_text, file_name, content_type, "
                " extracted_text, fetch_status, fetch_error, fetched_at, created_at) "
                "VALUES (2001, 'upload', 'broken', NULL, NULL, NULL, NULL, "
                "NULL, 'pending', NULL, NULL, '2026-10-16 09:00:00')"
            )
        )
    engine.dispose()


# ---------------------------------------------------------------------------
# Pre-existing rows survive
# ---------------------------------------------------------------------------


def test_phase9_migration_preserves_phase8_row_ids_and_timestamps(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase9-preserve.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        user_row = connection.execute(
            text("SELECT id, email, created_at FROM users WHERE id = 1901")
        ).one()
        entry_row = connection.execute(
            text("SELECT id, user_id, topic, created_at FROM study_entries WHERE id = 2001")
        ).one()
        quiz_set_row = connection.execute(
            text(
                "SELECT id, entry_id, user_id, generated_on, question_count, option_count "
                "FROM quiz_sets WHERE id = 2201"
            )
        ).one()
        attempt_row = connection.execute(
            text(
                "SELECT id, quiz_set_id, user_id, mode, total_questions, started_at, cursor, cursor_on_plan "
                "FROM quiz_attempts WHERE id = 2401"
            )
        ).one()
        # The Phase 9 reference_materials table must also be present after
        # the upgrade (otherwise pre-Phase-9 preservation is meaningless).
        ref_count = connection.execute(
            text(
                "SELECT count(*) FROM reference_materials WHERE entry_id = 2001"
            )
        ).scalar_one()
    engine.dispose()

    assert user_row == (1901, "phase8-p9@example.com", "2026-09-01 12:00:00")
    assert entry_row == (2001, 1901, "Topic9", "2026-09-01 13:00:00")
    assert quiz_set_row[0] == 2201
    assert quiz_set_row[1] == 2001
    assert attempt_row[0] == 2401
    assert attempt_row[1] == 2201
    assert attempt_row[6] == 0
    assert attempt_row[7] == 0
    assert ref_count == 0  # table exists; pre-Phase-9 entries have zero rows


# ---------------------------------------------------------------------------
# Round-trip: downgrade then upgrade restores shape and pre-existing rows
# ---------------------------------------------------------------------------


def test_phase9_migration_downgrade_drops_reference_materials_table(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase9-downgrade.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE9_REVISION)

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert EXPECTED_NEW_TABLES.isdisjoint(tables)
    # Phase 1-8 tables remain
    assert {
        "users",
        "study_entries",
        "review_schedules",
        "quiz_sets",
        "quiz_questions",
        "quiz_attempts",
        "quiz_answers",
        "interview_plans",
        "interview_turns",
    } <= tables

    # After downgrade the reference_materials table must not exist; the SQL
    # layer surfaces "no such table" when a caller SELECTs it. This pins the
    # post-downgrade shape (different from the round-trip forward-only test,
    # which confirms the table is present after upgrade).
    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        with pytest.raises(OperationalError):
            connection.execute(
                text("SELECT count(*) FROM reference_materials")
            ).scalar_one()
    engine.dispose()


def test_phase9_migration_upgrade_after_downgrade_restores_table_and_rows(
    monkeypatch, tmp_path
):
    try:
        database = tmp_path / "phase9-restore.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase8_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE9_REVISION)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    check_constraints = {
        c["name"] for c in inspector.get_check_constraints("reference_materials")
    }
    assert EXPECTED_NEW_TABLES <= tables
    assert EXPECTED_CHECK_CONSTRAINT in check_constraints

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        user_count = connection.execute(
            text("SELECT count(*) FROM users WHERE id = 1901")
        ).scalar_one()
        entry_count = connection.execute(
            text("SELECT count(*) FROM study_entries WHERE id = 2001")
        ).scalar_one()
    engine.dispose()

    assert user_count == 1
    assert entry_count == 1
