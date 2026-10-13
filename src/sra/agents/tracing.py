"""Configure LangSmith for an agent invocation using the app's .env settings."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from langsmith import Client, tracing_context

from sra.config import get_settings


@lru_cache
def _get_client() -> Client:
    settings = get_settings()
    return Client(
        api_key=settings.langsmith_api_key.get_secret_value(),
        api_url=settings.langsmith_endpoint.rstrip("/"),
        workspace_id=settings.langsmith_workspace_id or None,
    )


@contextmanager
def trace_agent() -> Iterator[None]:
    """Trace a graph and its nested model/tool calls when explicitly enabled.

    Pydantic reads .env into Settings without exporting it to os.environ.
    Passing a client and project explicitly lets LangSmith use those settings,
    while the context keeps tracing scoped to this invocation. When disabled,
    no LangSmith client is created and no traces are uploaded.
    """
    settings = get_settings()
    with tracing_context(
        enabled=settings.langsmith_tracing,
        project_name=settings.langsmith_project,
        client=_get_client() if settings.langsmith_tracing else None,
    ):
        yield
