"""Submit study notes to the HTTP API without loading server dependencies."""

from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError, field_validator

from sra.config import get_client_settings

# The server allows two 30-second model attempts; leave room for its DB work.
HTTP_TIMEOUT_SECONDS = 90.0


class _EntryResponse(BaseModel):
    """Client-side wire contract, independent of the server's response models."""

    id: int
    topic: str
    subtopic: str | None
    tags: list[str]
    summary: str
    created_at: str

    @field_validator("created_at")
    @classmethod
    def validate_iso_datetime(cls, value: str) -> str:
        # Require a time component, not a bare date or an epoch string.
        if not any(separator in value for separator in ("T", "t", " ")):
            raise ValueError("created_at must be an ISO datetime with a time component")
        datetime.fromisoformat(value)
        return value


_ENTRY_LIST = TypeAdapter(list[_EntryResponse])


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
    # HTTPX's default transport has retries=0, including for proxy connections.
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False)


def ingest_transcript(transcript: str, *, credential: str | None = None) -> list[dict[str, Any]]:
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
    base_url = get_client_settings().api_base_url.rstrip("/")
    url = f"{base_url}/api/v1/entries/ingest"
    headers = {"Authorization": f"Bearer {credential}"} if credential is not None else None
    try:
        with _create_http_client() as client:
            response = client.post(url, json={"transcript": transcript}, headers=headers)
    except httpx.TimeoutException as exc:
        raise ApiClientError(
            f"Submission to {url} timed out. The server may already have saved the note; "
            "check search before submitting again. No automatic retry was attempted."
        ) from (None if credential is not None else exc)
    except httpx.ConnectError as exc:
        if credential is not None:
            raise ApiClientError("Could not connect to the server. Please try again.") from None
        raise ApiClientError(f"Could not connect to {url}: {exc}") from exc
    except httpx.RequestError as exc:
        if credential is not None:
            raise ApiClientError(
                "The HTTP request failed. The save outcome is uncertain; check search before submitting again. "
                "No automatic retry was attempted."
            ) from None
        raise ApiClientError(
            f"HTTP request to {url} failed: {exc}. The save outcome is uncertain; "
            "check search before submitting again. No automatic retry was attempted."
        ) from exc

    if response.status_code != 201:
        if credential is not None:
            # An upstream proxy may echo request headers in its error page.
            hint = " Please sign in again." if response.status_code in {401, 403} else " Please check the server."
            raise ApiClientError(
                f"Ingestion returned HTTP {response.status_code}.{hint}", status_code=response.status_code,
            )
        detail = None
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = body.get("detail")
        message = detail if detail is not None else response.text.strip() or response.reason_phrase
        raise ApiClientError(
            f"Ingestion returned HTTP {response.status_code}: {message}",
            status_code=response.status_code,
            detail=detail,
        )

    try:
        entries = response.json()
    except ValueError as exc:
        raise ApiClientError("Invalid JSON in the ingestion response.", status_code=201) from (
            None if credential is not None else exc
        )

    try:
        # Strict validation rejects field coercion (e.g. a string or boolean ID).
        # Return the original dictionaries so timestamp strings and future
        # server fields are preserved verbatim.
        _ENTRY_LIST.validate_python(entries, strict=True)
    except ValidationError as exc:
        if credential is not None:
            raise ApiClientError("Invalid entries in the ingestion response.", status_code=201) from None
        raise ApiClientError(f"Invalid entries in the ingestion response: {exc}", status_code=201) from exc
    return entries
