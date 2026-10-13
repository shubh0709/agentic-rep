"""SRA-201 learning targets for submitting text to the Phase 1 HTTP API."""

from typing import Any

import httpx

from sra.config import get_client_settings  # noqa: F401 -- implementation dependency

HTTP_TIMEOUT_SECONDS = 90.0


class ApiClientError(Exception):
    """A user-facing submission failure, with HTTP details when available."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _create_http_client() -> httpx.Client:
    """SRA-201: build a synchronous HTTP client with a finite 90-second timeout.

    This budget leaves time for the server's model call. Disable automatic
    retries and redirect following: one user action must make one POST only.
    Return the client without submitting anything; ingest_transcript owns its
    lifetime and closes it on both success and failure. Tests replace this
    factory's transport so the real wrapper can reach FastAPI in process.
    """
    raise NotImplementedError("SRA-201: create the HTTP client with a finite timeout")


def ingest_transcript(transcript: str) -> list[dict[str, Any]]:
    """SRA-201: submit text once and return validated entry dictionaries.

    Read get_client_settings(), strip a trailing slash from api_base_url, and
    POST {"transcript": transcript} to /api/v1/entries/ingest using the client
    from _create_http_client(). Never send audio, user IDs, or server secrets.
    Close the client on every path, including response validation failures.

    Require HTTP 201 and JSON containing a list of objects shaped like EntryOut:
    integer id; string topic, summary, created_at (a valid ISO datetime);
    nullable string subtopic; list of string tags. Validate without importing
    server routes or schemas. Preserve extra fields and allow an empty list.

    Wrap transport, timeout, invalid JSON, and malformed-response failures in
    ApiClientError, chaining the original cause when there is one. For HTTP
    failures preserve status_code and FastAPI's detail, including structured
    details, and provide useful feedback for non-JSON error bodies. Timeouts
    after submission have an unknown outcome: explain that the server may
    already have saved the note. Never automatically retry a POST.
    """
    raise NotImplementedError("SRA-201: submit a transcript and validate the response")
