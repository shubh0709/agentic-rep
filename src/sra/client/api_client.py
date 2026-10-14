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

    retry_after: int | None = None

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


def _extract_detail(response: httpx.Response) -> object | None:
    """Pull FastAPI's structured `detail` out of a JSON error body, if present."""
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        return body.get("detail")
    return None


def _auth_required_error(response: httpx.Response, url: str) -> ApiClientError:
    detail = _extract_detail(response)
    suffix = f" Server said: {detail}." if detail is not None else ""
    return ApiClientError(
        f"auth_required for {url}.{suffix}",
        status_code=response.status_code,
        detail=detail,
    )


def _rate_limited_error(response: httpx.Response, url: str) -> ApiClientError:
    detail = _extract_detail(response)
    retry_after_header = response.headers.get("retry-after")
    retry_after: int | None = None
    if retry_after_header is not None:
        try:
            retry_after = int(retry_after_header)
        except ValueError:
            retry_after = None
    suffix = f" Retry after {retry_after_header} seconds." if retry_after_header is not None else ""
    error = ApiClientError(
        f"rate_limited for {url}.{suffix}",
        status_code=response.status_code,
        detail=detail,
    )
    error.retry_after = retry_after
    return error


def _http_error(response: httpx.Response, url: str, label: str) -> ApiClientError:
    detail = _extract_detail(response)
    message = detail if detail is not None else (response.text.strip() or response.reason_phrase)
    return ApiClientError(
        f"{label} returned HTTP {response.status_code}: {message}",
        status_code=response.status_code,
        detail=detail,
    )


def _authed_get(path: str, credential: str | None) -> tuple[httpx.Response, str]:
    """Issue a single GET with the resolved bearer header; close the client on every path."""
    base_url = get_client_settings().api_base_url.rstrip("/")
    url = f"{base_url}{path}"
    headers = {"Authorization": f"Bearer {credential}"} if credential is not None else None
    with _create_http_client() as client:
        return client.get(url, headers=headers), url


def _authed_post(path: str, body: dict[str, Any], credential: str | None) -> tuple[httpx.Response, str]:
    """Issue a single POST with the resolved bearer header; close the client on every path."""
    base_url = get_client_settings().api_base_url.rstrip("/")
    url = f"{base_url}{path}"
    headers = {"Authorization": f"Bearer {credential}"} if credential is not None else None
    with _create_http_client() as client:
        return client.post(url, json=body, headers=headers), url


def get_digest_ready(*, credential: str | None = None) -> bool:
    """SRA-506: GET /api/v1/digest/ready -> bool.

    Returns the per-user "ready" flag wakeup.py polls before calling
    get_digest_text(). 401 -> ApiClientError("auth_required"); 429 ->
    ApiClientError("rate_limited") with `retry_after` attached when the
    server returned Retry-After; any other non-200 -> ApiClientError with
    the original status and FastAPI detail.
    """
    response, url = _authed_get("/api/v1/digest/ready", credential)

    if response.status_code == 401:
        raise _auth_required_error(response, url)
    if response.status_code == 429:
        raise _rate_limited_error(response, url)
    if response.status_code != 200:
        raise _http_error(response, url, "digest/ready")

    try:
        body = response.json()
    except ValueError as exc:
        raise ApiClientError("Invalid JSON in the digest/ready response.", status_code=200) from exc

    if not isinstance(body, dict) or not isinstance(body.get("ready"), bool):
        raise ApiClientError("Unexpected digest/ready response shape.", status_code=200)

    return body["ready"]


def get_digest_text(*, credential: str | None = None) -> str:
    """SRA-506: GET /api/v1/digest -> one spoken sentence.

    Formats the returned ReviewOut[] into
    `"You have N reviews due today: <topic 1>, <topic 2>, and <topic M>."`.
    An empty digest returns `"You have no reviews due today."`.
    Raises ApiClientError on 401 (auth_required), 429 (rate_limited), or
    any other non-200 HTTP failure, preserving status_code.
    """
    response, url = _authed_get("/api/v1/digest", credential)

    if response.status_code == 401:
        raise _auth_required_error(response, url)
    if response.status_code == 429:
        raise _rate_limited_error(response, url)
    if response.status_code != 200:
        raise _http_error(response, url, "digest")

    try:
        rows = response.json()
    except ValueError as exc:
        raise ApiClientError("Invalid JSON in the digest response.", status_code=200) from exc

    if not isinstance(rows, list):
        raise ApiClientError("Digest response was not a list.", status_code=200)

    if not rows:
        return "You have no reviews due today."

    topics: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry = row.get("entry")
        if isinstance(entry, dict):
            topic = entry.get("topic")
            if isinstance(topic, str):
                topics.append(topic)

    count = len(rows)
    if not topics:
        return f"You have {count} reviews due today."
    if count == 1:
        body = topics[0]
    else:
        body = ", ".join(topics[:-1]) + f", and {topics[-1]}"
    return f"You have {count} reviews due today: {body}."


def start_voice_quiz_attempt(quiz_set_id: int, *, credential: str | None = None) -> dict[str, Any]:
    """SRA-506: POST /api/v1/quiz/{quiz_set_id}/attempts {"mode": "voice"}.

    Returns the server's `{attempt_id, question_text, options}` payload.
    The Phase 4 `400 "Voice mode is not yet supported"` leftover is mapped
    to ApiClientError("server_phase5_pending") so the desktop fails fast
    against a pre-Phase-5 server. 401 -> auth_required; 429 -> rate_limited.
    """
    response, url = _authed_post(f"/api/v1/quiz/{quiz_set_id}/attempts", {"mode": "voice"}, credential)

    if response.status_code in (200, 201):
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiClientError(
                "Invalid JSON in the voice attempt response.",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise ApiClientError(
                "Voice attempt response was not an object.",
                status_code=response.status_code,
            )
        return payload

    if response.status_code == 400:
        detail = _extract_detail(response)
        if isinstance(detail, str) and "not yet supported" in detail.lower():
            raise ApiClientError(
                f"server_phase5_pending: {detail}",
                status_code=400,
                detail=detail,
            )
        raise ApiClientError(
            f"Voice attempt rejected: {detail if detail is not None else response.text}",
            status_code=400,
            detail=detail,
        )
    if response.status_code == 401:
        raise _auth_required_error(response, url)
    if response.status_code == 429:
        raise _rate_limited_error(response, url)
    raise _http_error(response, url, "voice attempt")


def send_voice_turn(
    attempt_id: int,
    answer_text: str,
    *,
    credential: str | None = None,
) -> dict[str, Any]:
    """SRA-506: POST /api/v1/quiz/attempts/{attempt_id}/voice-turn.

    Body `{"answer_text": answer_text}`. Returns the server's
    `VoiceTurnResponse` dict. 409 ("Attempt already completed") maps to
    ApiClientError("attempt_completed"). 401 -> auth_required;
    429 -> rate_limited.
    """
    response, url = _authed_post(
        f"/api/v1/quiz/attempts/{attempt_id}/voice-turn",
        {"answer_text": answer_text},
        credential,
    )

    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiClientError("Invalid JSON in the voice turn response.", status_code=200) from exc
        if not isinstance(payload, dict):
            raise ApiClientError("Voice turn response was not an object.", status_code=200)
        return payload

    if response.status_code == 409:
        detail = _extract_detail(response)
        raise ApiClientError(
            f"attempt_completed: {detail if detail is not None else 'Attempt already completed.'}",
            status_code=409,
            detail=detail,
        )
    if response.status_code == 401:
        raise _auth_required_error(response, url)
    if response.status_code == 429:
        raise _rate_limited_error(response, url)
    raise _http_error(response, url, "voice turn")
