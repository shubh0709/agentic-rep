"""SRA-301 identity-schema contracts, written before the migration."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from sra.db.models import User

pytestmark = pytest.mark.phase3


def test_oauth_user_defaults_to_user_role_and_no_login_timestamp():
    user = User(
        email="learner@example.com",
        display_name="Learner",
        google_sub="google-subject-1",
    )

    assert user.google_sub == "google-subject-1"
    assert user.role == "user"
    assert user.last_login_at is None


@pytest.mark.parametrize("role", ["user", "admin"])
def test_only_supported_roles_are_accepted(role):
    user = User(
        email=f"{role}@example.com",
        display_name=role.title(),
        google_sub=f"subject-{role}",
        role=role,
    )

    assert user.role == role


@pytest.mark.parametrize("role", ["", "owner", "superuser", "ADMIN", None])
def test_unknown_roles_are_rejected_at_model_boundary(role):
    with pytest.raises(ValidationError):
        User.model_validate(
            {
                "email": "learner@example.com",
                "display_name": "Learner",
                "google_sub": "subject-1",
                "role": role,
            }
        )


@pytest.mark.parametrize("subject", ["", " ", "\t"])
def test_non_null_google_subject_must_not_be_blank(subject):
    with pytest.raises(ValidationError):
        User.model_validate(
            {
                "email": "learner@example.com",
                "display_name": "Learner",
                "google_sub": subject,
            }
        )


def test_last_login_accepts_timezone_aware_datetime():
    logged_in_at = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)

    user = User(
        email="learner@example.com",
        display_name="Learner",
        google_sub="subject-1",
        last_login_at=logged_in_at,
    )

    assert user.last_login_at == logged_in_at


def test_google_subject_is_unique_in_the_database():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(User(email="one@example.com", display_name="One", google_sub="same-subject"))
        session.commit()
        session.add(User(email="two@example.com", display_name="Two", google_sub="same-subject"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_distinct_google_subjects_can_be_stored():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(User(email="one@example.com", display_name="One", google_sub="subject-1"))
        session.add(User(email="two@example.com", display_name="Two", google_sub="subject-2"))
        session.commit()

        assert len(session.exec(select(User)).all()) == 2


def test_legacy_user_can_remain_unlinked_without_becoming_admin():
    user = User(email="local@localhost", display_name="Local User", google_sub=None)

    assert user.google_sub is None
    assert user.role == "user"


def test_database_defaults_role_and_rejects_invalid_role_or_blank_subject():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (email, display_name, google_sub, created_at) VALUES (:email, :name, :sub, :now)"),
            {"email": "default@example.com", "name": "Default", "sub": "subject-default", "now": "2026-10-14 09:30:00"},
        )
        assert connection.execute(text("SELECT role FROM users")).scalar_one() == "user"

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO users (email, display_name, google_sub, role, created_at) "
                    "VALUES (:email, :name, :sub, :role, :now)"
                ),
                {"email": "owner@example.com", "name": "Owner", "sub": "subject-owner", "role": "owner", "now": "2026-10-14 09:30:00"},
            )

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO users (email, display_name, google_sub, role, created_at) "
                    "VALUES (:email, :name, :sub, :role, :now)"
                ),
                {"email": "blank@example.com", "name": "Blank", "sub": " ", "role": "user", "now": "2026-10-14 09:30:00"},
            )
