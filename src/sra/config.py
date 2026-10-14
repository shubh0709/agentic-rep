"""Application configuration, loaded once from environment variables / .env."""

from functools import lru_cache
from typing import Literal, Self
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
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_callback_url: str = "http://127.0.0.1:8000/api/v1/auth/google/callback"
    google_authorization_endpoint: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_endpoint: str = "https://oauth2.googleapis.com/token"
    auth_signing_secret: SecretStr = SecretStr("")
    auth_credential_ttl_seconds: int = 3600
    oauth_state_ttl_seconds: int = 600
    desktop_completion_ttl_seconds: int = 60
    auth_cookie_name: str = "sra_session"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict"] = "lax"
    auth_web_redirect_url: str = "/"

    @field_validator("google_callback_url")
    @classmethod
    def validate_google_callback_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("GOOGLE_CALLBACK_URL must be an absolute HTTP(S) URL without query or fragment")
        return value

    @field_validator("auth_credential_ttl_seconds", "oauth_state_ttl_seconds", "desktop_completion_ttl_seconds")
    @classmethod
    def validate_positive_auth_ttl(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("authentication lifetimes must be positive")
        return value

    @field_validator("auth_cookie_samesite")
    @classmethod
    def validate_cookie_samesite(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"lax", "strict"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be lax or strict")
        return normalized

    @model_validator(mode="after")
    def validate_tracing_credentials(self) -> Self:
        if self.langsmith_tracing and not self.langsmith_api_key.get_secret_value().strip():
            raise ValueError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true")
        return self

    def require_auth_configuration(self) -> None:
        """Fail safely at an auth entry point when server secrets are absent."""
        required = (
            self.google_client_id,
            self.google_client_secret.get_secret_value(),
            self.auth_signing_secret.get_secret_value(),
        )
        if not all(value.strip() for value in required):
            raise ValueError("Google OAuth is not configured")
        if len(self.auth_signing_secret.get_secret_value()) < 32:
            raise ValueError("AUTH_SIGNING_SECRET must contain at least 32 characters")
        callback = urlsplit(self.google_callback_url)
        if not self.auth_cookie_secure and callback.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("AUTH_COOKIE_SECURE may be disabled only for localhost development")


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
    desktop_login_timeout_seconds: int = 120
    keychain_service_prefix: str = "com.spaced-repetition-agent.desktop"

    @field_validator("desktop_login_timeout_seconds")
    @classmethod
    def validate_desktop_login_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("DESKTOP_LOGIN_TIMEOUT_SECONDS must be positive")
        return value

    @field_validator("keychain_service_prefix")
    @classmethod
    def validate_keychain_service_prefix(cls, value: str) -> str:
        if not value.strip() or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("KEYCHAIN_SERVICE_PREFIX must be a non-empty value without whitespace")
        return value

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
