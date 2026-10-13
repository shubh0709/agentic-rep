import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import sra.db.models  # noqa: F401  (registers tables on SQLModel.metadata)
from sra.config import get_settings


@pytest.fixture(autouse=True)
def disable_live_tracing(monkeypatch):
    """Keep tests offline even when the developer has enabled tracing locally."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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

    user = User(email="test@example.com", display_name="Test User")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def other_user(session):
    from sra.db.models import User

    user = User(email="other@example.com", display_name="Other User")
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
    from fastapi.testclient import TestClient

    from sra.api.app import create_app
    from sra.db.session import get_session

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)
