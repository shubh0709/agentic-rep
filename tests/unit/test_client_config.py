"""SRA-201/202/205 contracts; these start red until the learning stubs are filled."""

import pytest
from pydantic import ValidationError

from sra.config import ClientSettings, get_client_settings, get_settings

pytestmark = pytest.mark.phase2


@pytest.fixture(autouse=True)
def isolated_client_environment(monkeypatch, tmp_path):
    """No local .env or shell credentials may accidentally satisfy a test."""
    monkeypatch.chdir(tmp_path)
    for name in (
        "API_BASE_URL", "WHISPER_MODEL_SIZE", "HOTKEY_COMBO", "OPENAI_API_KEY",
        "OPENAI_MODEL", "DATABASE_URL", "LANGSMITH_API_KEY", "LANGSMITH_TRACING",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    get_client_settings.cache_clear()
    get_settings.cache_clear()
    yield
    get_client_settings.cache_clear()
    get_settings.cache_clear()


def test_defaults_load_without_any_server_credentials():
    settings = get_client_settings()

    assert settings.api_base_url == "http://127.0.0.1:8000"
    assert settings.whisper_model_size == "base"
    assert settings.hotkey_combo == "<ctrl>+<alt>+r"
    assert {"openai_api_key", "langsmith_api_key", "database_url", "google_client_secret"}.isdisjoint(
        type(settings).model_fields
    )


def test_environment_overrides_client_defaults(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", "https://study.example.test:8443/")
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "tiny")
    monkeypatch.setenv("HOTKEY_COMBO", "<ctrl>+<shift>+s")

    settings = get_client_settings()

    assert settings.api_base_url == "https://study.example.test:8443/"
    assert settings.whisper_model_size == "tiny"
    assert settings.hotkey_combo == "<ctrl>+<shift>+s"


def test_dotenv_client_settings_and_environment_precedence(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "API_BASE_URL=http://localhost:9000\nWHISPER_MODEL_SIZE=small\n"
        "HOTKEY_COMBO=<ctrl>+<alt>+s\nOPENAI_API_KEY=server-only-secret\n"
        "LANGSMITH_TRACING=true\nUNRELATED_SERVER_SETTING=ignored\n"
    )

    settings = get_client_settings()

    assert settings.api_base_url == "http://localhost:9000"
    assert settings.whisper_model_size == "small"
    assert settings.hotkey_combo == "<ctrl>+<alt>+s"
    assert "server-only-secret" not in repr(settings)

    monkeypatch.setenv("API_BASE_URL", "https://override.example.test")
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "tiny")
    monkeypatch.setenv("HOTKEY_COMBO", "<ctrl>+<shift>+r")
    get_client_settings.cache_clear()
    overridden = get_client_settings()
    assert overridden.api_base_url == "https://override.example.test"
    assert overridden.whisper_model_size == "tiny"
    assert overridden.hotkey_combo == "<ctrl>+<shift>+r"


def test_client_loading_does_not_call_server_settings(monkeypatch):
    def forbidden_server_settings():
        pytest.fail("Desktop configuration must not load server settings")

    monkeypatch.setattr("sra.config.get_settings", forbidden_server_settings)
    assert get_client_settings().api_base_url == "http://127.0.0.1:8000"


def test_server_still_requires_openai_key_after_client_load():
    get_client_settings()
    with pytest.raises(ValidationError, match="openai_api_key"):
        get_settings()


def test_client_cache_retains_first_load_until_explicitly_cleared(monkeypatch):
    first = get_client_settings()
    monkeypatch.setenv("API_BASE_URL", "http://localhost:9001")

    assert get_client_settings() is first
    assert get_client_settings().api_base_url == "http://127.0.0.1:8000"
    get_client_settings.cache_clear()
    second = get_client_settings()
    assert second is not first
    assert second.api_base_url == "http://localhost:9001"


def test_server_and_client_caches_are_independent(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-test")
    server = get_settings()
    client = get_client_settings()

    get_client_settings.cache_clear()
    assert get_settings() is server
    assert get_client_settings() is not client
    refreshed_client = get_client_settings()
    get_settings.cache_clear()
    assert get_client_settings() is refreshed_client
    assert get_settings() is not server


@pytest.mark.parametrize("url", [
    "http://localhost:8000", "http://127.0.0.1:8000/", "https://study.example.test",
    "https://study.example.test/service", "http://[::1]:8000",
])
def test_usable_server_urls_are_accepted(url):
    assert ClientSettings(api_base_url=url).api_base_url == url


@pytest.mark.parametrize("url", [
    "", "localhost:8000", "ftp://study.example.test", "file:///tmp/study",
    "http://", "https:///missing-host", "http://host:invalid", "http://host:99999",
    "http://bad host:8000", "https://user:secret@study.example.test",
    "https://study.example.test?token=secret", "https://study.example.test#fragment",
])
def test_unusable_server_urls_report_the_setting_name(url):
    with pytest.raises(ValidationError, match="api_base_url"):
        ClientSettings(api_base_url=url)
