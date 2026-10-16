import pytest
from pydantic import ValidationError

from sra.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_load_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("OPENAI_MODEL", "DATABASE_URL", "WHISPER_MODEL_SIZE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = get_settings()
    assert settings.openai_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(settings)
    assert settings.openai_model == "gpt-4.1-nano"
    assert settings.database_url == "sqlite:///./sra.db"
    assert settings.whisper_model_size == "base"


def test_settings_missing_required_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env file here, so nothing can supply the key
    with pytest.raises(ValidationError, match="openai_api_key"):
        get_settings()


def test_openai_settings_load_from_dotenv_and_env_model_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-dotenv-test\nOPENAI_MODEL=gpt-4.1-nano\n")

    settings = get_settings()
    assert settings.openai_api_key.get_secret_value() == "sk-dotenv-test"
    assert settings.openai_model == "gpt-4.1-nano"

    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    get_settings.cache_clear()
    assert get_settings().openai_model == "gpt-4.1-mini"


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert get_settings() is get_settings()


def test_tracing_settings_load_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    (tmp_path / ".env").write_text(
        "LANGSMITH_TRACING=true\n"
        "LANGSMITH_API_KEY=ls-test-secret\n"
        "LANGSMITH_PROJECT=study-dev\n"
        "LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com\n"
        "LANGSMITH_WORKSPACE_ID=test-workspace\n"
    )

    settings = get_settings()

    assert settings.langsmith_tracing is True
    assert settings.langsmith_api_key.get_secret_value() == "ls-test-secret"
    assert "ls-test-secret" not in repr(settings)
    assert settings.langsmith_project == "study-dev"
    assert settings.langsmith_endpoint == "https://eu.api.smith.langchain.com"
    assert settings.langsmith_workspace_id == "test-workspace"


@pytest.mark.parametrize("api_key", ["", "   "])
def test_enabled_tracing_requires_api_key(monkeypatch, tmp_path, api_key):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", api_key)

    with pytest.raises(ValidationError, match="LANGSMITH_API_KEY is required"):
        get_settings()


def test_auth_refresh_ttl_default_is_one_week(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("AUTH_REFRESH_TTL_SECONDS",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    settings = get_settings()

    assert settings.auth_refresh_ttl_seconds == 604800


@pytest.mark.parametrize("ttl", [0, -1, -604800])
def test_auth_refresh_ttl_must_be_positive(monkeypatch, tmp_path, ttl):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_REFRESH_TTL_SECONDS", str(ttl))

    with pytest.raises(ValidationError, match="auth_refresh_ttl_seconds"):
        get_settings()


def test_auth_refresh_ttl_accepts_large_values(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_REFRESH_TTL_SECONDS", "2592000")

    settings = get_settings()

    assert settings.auth_refresh_ttl_seconds == 2592000
