import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import sra.db.models  # noqa: F401  (registers tables on SQLModel.metadata)
from sra.config import get_client_settings, get_settings


@pytest.fixture(autouse=True)
def disable_live_tracing(monkeypatch):
    """Keep tests offline even when the developer has enabled tracing locally."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    get_settings.cache_clear()
    get_client_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_client_settings.cache_clear()


@pytest.fixture
def session():
    """An isolated in-memory SQLite DB, fresh for every test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def seeded_user(session):
    from sra.db.models import User

    user = session.exec(select(User).where(User.google_sub == "test-subject")).first()
    if user is not None:
        return user
    user = User(email="test@example.com", display_name="Test User", google_sub="test-subject")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def other_user(session):
    from sra.db.models import User

    user = User(email="other@example.com", display_name="Other User", google_sub="other-subject")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def client(monkeypatch, session):
    """
    A TestClient for create_app(), with the DB dependency overridden
    to the shared in-memory `session` fixture (so requests and test
    assertions see the same data) and table creation short-circuited
    (the `session` fixture already created them against its own engine).
    """
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from sra.api.app import create_app
    from sra.auth.google_oauth import issue_app_credential
    from sra.config import get_settings
    from sra.db.models import User
    from sra.db.session import get_session

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "rate-limit-test-signing-secret-with-entropy")
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    get_settings.cache_clear()

    user = session.exec(select(User).where(User.google_sub == "test-subject")).first()
    if user is None:
        user = User(email="test@example.com", display_name="Test User", google_sub="test-subject")
        session.add(user)
        session.commit()
        session.refresh(user)
    assert user.id is not None
    credential = issue_app_credential(
        user.id,
        signing_secret="rate-limit-test-signing-secret-with-entropy",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, headers={"Authorization": f"Bearer {credential}"})
