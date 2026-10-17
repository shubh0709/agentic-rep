# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — Alembic migration rehearsal for ``quiz_questions`` Phase 9 columns.

A NEW alembic revision chained off ``f6a7b8c9d0e1`` must add the three
additive nullable columns ``bloom_level``, ``source_passage_quote``,
``distractor_misconception`` to ``quiz_questions``. ``downgrade`` drops
them; pre-Phase-9 quiz rows survive both directions.

Mirrors the style of ``tests/integration/test_phase9_migration.py`` and
``tests/integration/test_phase8_migration.py``. The migration file is
owned by SRA-903 step 3; this test is expected to fail today because the
file does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text

from sra.config import get_settings

pytestmark = pytest.mark.phase9

ROOT = Path(__file__).resolve().parents[2]
PRE_PHASE9_REVISION = "f6a7b8c9d0e1"  # chains off the Phase 9 reference materials revision

EXPECTED_NEW_COLUMNS = {
    "bloom_level",
    "source_passage_quote",
    "distractor_misconception",
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


def _seed_phase9_reference_database(config: Config, database: Path) -> None:
    """Seed the DB at the Phase 9 reference-materials revision with a
    pre-Phase-9 QuizQuestion row (one whose three Phase 9 columns are NULL).
    """
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
                "VALUES (3901, 'phase9-q@example.com', 'Ph9Q', 'phase9-q-sub', 'user', NULL, "
                "'2026-09-01 12:00:00', '09:00', 'UTC', 10, 4, 6, 5, NULL, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (3001, 'TopicQA', NULL, '[\"phase9\"]', 'summary', 'transcript', "
                "3901, '2026-09-01 13:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_sets "
                "(id, entry_id, user_id, generated_on, question_count, option_count) "
                "VALUES (3201, 3001, 3901, '2026-10-16', 1, 4)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quiz_questions "
                "(id, quiz_set_id, question_text, options, correct_option_index, explanation, order_index) "
                "VALUES (3301, 3201, 'pre-p9 q0', '[\"a\",\"b\",\"c\",\"d\"]', 0, NULL, 0)"
            )
        )
    engine.dispose()


# ---------------------------------------------------------------------------
# Forward migration adds the three columns, all nullable
# ---------------------------------------------------------------------------


def test_phase9_quiz_migration_adds_three_columns(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase9-qcols.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase9_reference_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    columns = {c["name"] for c in inspector.get_columns("quiz_questions")}
    missing = EXPECTED_NEW_COLUMNS - columns
    assert not missing, f"missing columns: {missing}"


@pytest.mark.parametrize("column_name", sorted(EXPECTED_NEW_COLUMNS))
def test_phase9_quiz_migration_column_is_nullable(monkeypatch, tmp_path, column_name):
    """Each of the three Phase 9 columns must allow NULL (legacy rows survive)."""
    try:
        database = tmp_path / f"phase9-null-{column_name}.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase9_reference_database(config, database)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    cols = {c["name"]: c for c in inspector.get_columns("quiz_questions")}
    assert cols[column_name]["nullable"] is True, (
        f"column {column_name!r} must be nullable so legacy rows survive"
    )


# ---------------------------------------------------------------------------
# Round-trip: downgrade then upgrade restores shape and pre-existing rows
# ---------------------------------------------------------------------------


def test_phase9_quiz_migration_preserves_pre_phase9_quiz_row(monkeypatch, tmp_path):
    """Pre-Phase-9 quiz rows survive the forward migration unchanged."""
    try:
        database = tmp_path / "phase9-preserve-quiz.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase9_reference_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, quiz_set_id, question_text, options, correct_option_index, "
                "explanation, order_index FROM quiz_questions WHERE id = 3301"
            )
        ).one()
        # None of the new columns should be populated on a pre-Phase-9 row.
        for col in EXPECTED_NEW_COLUMNS:
            value = connection.execute(
                text(f"SELECT {col} FROM quiz_questions WHERE id = 3301")
            ).scalar_one()
            assert value is None, (
                f"pre-Phase-9 column {col!r} should be NULL on a legacy row; got {value!r}"
            )
    engine.dispose()

    assert row[0] == 3301
    assert row[1] == 3201
    assert row[2] == "pre-p9 q0"
    assert row[6] == 0


def test_phase9_quiz_migration_downgrade_drops_three_columns(monkeypatch, tmp_path):
    try:
        database = tmp_path / "phase9-down-quiz.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase9_reference_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE9_REVISION)

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    columns = {c["name"] for c in inspector.get_columns("quiz_questions")}
    assert EXPECTED_NEW_COLUMNS.isdisjoint(columns)
    # The legacy quiz row must still be readable.
    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM quiz_questions WHERE id = 3301")
        ).scalar_one()
    engine.dispose()
    assert count == 1


def test_phase9_quiz_migration_upgrade_after_downgrade_restores_columns(
    monkeypatch, tmp_path
):
    try:
        database = tmp_path / "phase9-restore-quiz.db"
    except (OSError, PermissionError):
        pytest.skip("Environment lacks a writable temp directory")
    config = _alembic_config(monkeypatch, database)
    _seed_phase9_reference_database(config, database)
    command.upgrade(config, "head")
    command.downgrade(config, PRE_PHASE9_REVISION)
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database}"))
    columns = {c["name"] for c in inspector.get_columns("quiz_questions")}
    assert EXPECTED_NEW_COLUMNS <= columns

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        quiz_row = connection.execute(
            text("SELECT count(*) FROM quiz_questions WHERE id = 3301")
        ).scalar_one()
    engine.dispose()
    assert quiz_row == 1
