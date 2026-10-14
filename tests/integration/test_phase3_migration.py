"""Rehearse SRA-301 against a real Phase 2 SQLite database file."""

from pathlib import Path
from shutil import copy2

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from sra.config import get_settings
from sra.db.legacy_identity import LegacyIdentityError, link_legacy_user

pytestmark = pytest.mark.phase3
ROOT = Path(__file__).parents[2]
PHASE2_REVISION = "067a64508521"
PHASE3_REVISION = "8cb1b420d9d2"


def _alembic_config(monkeypatch: pytest.MonkeyPatch, database: Path) -> Config:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-migration-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    return Config(ROOT / "alembic.ini")


def _seed_phase2_database(config: Config, database: Path) -> None:
    command.upgrade(config, PHASE2_REVISION)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, created_at) "
                "VALUES (41, 'local@localhost', 'Local User', '2026-01-02 03:04:05')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO study_entries "
                "(id, topic, subtopic, tags, summary, raw_transcript, user_id, created_at) "
                "VALUES (51, 'Mitosis', 'Prophase', '[\"biology\"]', 'Cell division', 'notes', 41, "
                "'2026-02-03 04:05:06')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO review_schedules "
                "(id, entry_id, interval_label, scheduled_date, status, completed_at) "
                "VALUES (61, 51, '1d', '2026-02-04', 'pending', NULL)"
            )
        )
    engine.dispose()


def test_upgrade_preserves_phase2_data_and_supports_explicit_legacy_link(monkeypatch, tmp_path):
    database = tmp_path / "phase2-upgrade.db"
    config = _alembic_config(monkeypatch, database)
    _seed_phase2_database(config, database)
    backup = tmp_path / "phase2-backup.db"
    copy2(database, backup)  # All seeding connections are closed before copying.

    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(f"sqlite:///{database}")
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("users")} >= {
        "google_sub",
        "role",
        "last_login_at",
    }
    assert any(index["name"] == "ix_users_google_sub" and index["unique"] for index in inspector.get_indexes("users"))

    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == PHASE3_REVISION
        assert connection.execute(
            text("SELECT id, email, display_name, google_sub, role, last_login_at, created_at FROM users")
        ).one() == (41, "local@localhost", "Local User", None, "user", None, "2026-01-02 03:04:05")
        assert connection.execute(
            text("SELECT id, user_id, topic, created_at FROM study_entries")
        ).one() == (51, 41, "Mitosis", "2026-02-03 04:05:06")
        assert connection.execute(
            text("SELECT id, entry_id, scheduled_date, status FROM review_schedules")
        ).one() == (61, 51, "2026-02-04", "pending")

    with Session(engine) as session:
        linked = link_legacy_user(user_id=41, google_sub="operator-verified-subject", session=session)
        assert linked.id == 41
        assert linked.role == "user"
        assert linked.google_sub == "operator-verified-subject"
        with pytest.raises(LegacyIdentityError, match="already linked"):
            link_legacy_user(user_id=41, google_sub="different-subject", session=session)

    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO users (email, display_name, google_sub, created_at) "
                "VALUES ('duplicate@example.com', 'Duplicate', 'operator-verified-subject', :created_at)"
            ),
            {"created_at": "2026-10-14 09:30:00"},
        )
    engine.dispose()

    recovered = tmp_path / "phase2-recovered.db"
    copy2(backup, recovered)
    recovery_engine = create_engine(f"sqlite:///{recovered}")
    with recovery_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == PHASE2_REVISION
        assert connection.execute(text("SELECT id FROM users")).scalar_one() == 41
        assert connection.execute(text("SELECT id, user_id FROM study_entries")).one() == (51, 41)
        assert connection.execute(text("SELECT id, entry_id FROM review_schedules")).one() == (61, 51)
    recovery_engine.dispose()


def test_downgrade_documents_identity_loss_but_preserves_owned_rows(monkeypatch, tmp_path):
    database = tmp_path / "phase3-downgrade.db"
    config = _alembic_config(monkeypatch, database)
    _seed_phase2_database(config, database)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE users SET google_sub = 'lost-on-downgrade', role = 'admin', "
                "last_login_at = '2026-10-14 09:30:00' WHERE id = 41"
            )
        )
    engine.dispose()

    command.downgrade(config, PHASE2_REVISION)

    engine = create_engine(f"sqlite:///{database}")
    assert {column["name"] for column in inspect(engine).get_columns("users")} == {
        "id",
        "email",
        "display_name",
        "created_at",
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == PHASE2_REVISION
        assert connection.execute(text("SELECT id, created_at FROM users")).one() == (41, "2026-01-02 03:04:05")
        assert connection.execute(text("SELECT id, user_id, created_at FROM study_entries")).one() == (
            51,
            41,
            "2026-02-03 04:05:06",
        )
        assert connection.execute(text("SELECT id, entry_id, scheduled_date FROM review_schedules")).one() == (
            61,
            51,
            "2026-02-04",
        )
    engine.dispose()
