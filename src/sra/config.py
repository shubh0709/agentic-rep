"""Application configuration, loaded once from environment variables / .env."""

from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central config object. Phase 1 only needs the fields below; later
    phases (OAuth, rate limiting, demo mode) will add fields to this
    same class rather than replace it, so anything reading Settings
    today keeps working unchanged.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr
    openai_model: str = "gpt-4.1-nano"
    database_url: str = "sqlite:///./sra.db"
    whisper_model_size: str = "base"
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "spaced-repetition-agent"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: str | None = None

    @model_validator(mode="after")
    def validate_tracing_credentials(self) -> Self:
        if self.langsmith_tracing and not self.langsmith_api_key.get_secret_value().strip():
            raise ValueError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true")
        return self


@lru_cache
def get_settings() -> Settings:
    """
    Return a process-wide cached Settings instance.

    Concept: the standard FastAPI pattern for injectable config --
    functools.lru_cache turns a plain constructor call into a
    memoized singleton, so every caller (routes, jobs, tests) shares
    one validated instance without reaching for a global variable.
    Pydantic-settings reads env vars (and `.env` via model_config
    above) automatically when you construct Settings() with no
    arguments.
    """
    # load env
    return Settings() # pyright: ignore[reportCallIssue]


class ClientSettings(BaseSettings):
    """Desktop-only settings; constructing these must never require server secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_base_url: str = "http://127.0.0.1:8000"
    whisper_model_size: str = "base"
    hotkey_combo: str = "<ctrl>+<alt>+r"

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        """SRA-201: validate an HTTP(S) server origin or base path.

        Accept localhost, IP addresses, HTTPS, and an optional trailing slash.
        Reject missing hosts, unsupported schemes, invalid ports, whitespace,
        embedded credentials, query strings, and fragments with ValueError so
        Pydantic reports which setting is invalid. Preserve a usable URL string;
        the HTTP wrapper owns joining it with /api/v1/entries/ingest.
        This client model deliberately has no server credential fields.
        """
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Server URL must not contain whitespace or control characters")
        if "\\" in value:
            raise ValueError("Server URL must not contain backslashes")

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Server URL must use HTTP(S) and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Server URL must not contain embedded credentials")
        if "?" in value or "#" in value:
            raise ValueError("Server URL must not contain a query string or fragment")

        # Accessing .port also rejects nonnumeric and out-of-range ports.
        port = parsed.port
        if parsed.netloc.endswith(":") or port == 0:
            raise ValueError("Server URL port must be between 1 and 65535")
        return value


@lru_cache
def get_client_settings() -> ClientSettings:
    """SRA-201: construct and cache ClientSettings, independently of Settings.

    Let Pydantic read environment variables and .env on the first call. Repeated
    calls must return the same object until cache_clear() is called. Never call
    get_settings(): the desktop only needs a server URL, Whisper model size, and
    hotkey combo, even when no server credentials are configured locally.
    """
    return ClientSettings()
