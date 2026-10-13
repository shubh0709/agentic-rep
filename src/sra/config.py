"""Application configuration, loaded once from environment variables / .env."""

from functools import lru_cache
from typing import Self

from pydantic import SecretStr, model_validator
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
    raise NotImplementedError
