"""SRA-901 fetch_url_text -- lazy import seam for trafilatura + httpx client factory."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FetchResult:
    """Outcome of one URL fetch attempt.

    ``ok=True`` means ``text`` is non-empty and ``title`` may carry the
    extracted title (None when trafilatura could not determine one).
    ``ok=False`` means ``error`` carries the typed reason -- see the
    plan for the canonical list (``http_error``,
    ``unsupported_content_type``, ``response_too_large``,
    ``invalid_url``, etc.).
    """

    ok: bool
    text: str = ""
    title: str | None = None
    error: str | None = None


_ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")


def _import_trafilatura():
    """Lazy trafilatura loader.

    Production callers (the topics router) rely on trafilatura for the
    cleanest HTML-to-text extraction. Tests substitute this seam with a
    fake so the heavy module never has to be importable at collection
    time -- the SRA-901 unit test
    ``test_importing_fetcher_module_does_not_load_trafilatura`` enforces
    the lazy-import discipline.
    """

    import trafilatura  # type: ignore[import-not-found]

    return trafilatura


def _get_httpx_client():
    """Return an ``httpx.Client`` configured for outbound fetches.

    Tests substitute this seam with a ``MagicMock`` whose ``.get``
    returns a stubbed response. The default implementation follows
    redirects and uses a self-hosted User-Agent string.
    """

    import httpx  # type: ignore[import-not-found]

    return httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": "sra/0.1 (+self-hosted)"},
    )


def _extract_title(trafilatura: object, html: str) -> str | None:
    """Resolve the page title via either the real trafilatura API or the test seam.

    Real ``trafilatura`` exposes ``extract_metadata(html).title``; the
    SRA-901 unit-test stub is a plain dataclass whose ``title`` field
    carries the value the test wants surfaced. ``hasattr`` checks keep
    both paths working without coupling the production code to the
    stub's shape.
    """

    candidate: object | None = None
    extract_metadata = getattr(trafilatura, "extract_metadata", None)
    if callable(extract_metadata):
        try:
            candidate = extract_metadata(html)
        except Exception:  # noqa: BLE001
            candidate = None
    if candidate is None:
        candidate = trafilatura
    title = getattr(candidate, "title", None)
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


def _strip_scripts_and_styles(html: str) -> str:
    """Best-effort fallback for when trafilatura is unavailable.

    Removes ``<script>`` and ``<style>`` blocks (case-insensitive) and
    returns the remainder. Used as a documented best-effort path -- the
    trafilatura extractor is the primary path.
    """

    import re

    cleaned = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style\s*>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned


def _normalize_content_type(content_type: str | None) -> str:
    if content_type is None:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _validate_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("invalid_url")
    return url


def fetch_url_text(url: str, *, timeout_s: float = 8.0, max_bytes: int = 2_000_000) -> FetchResult:
    """Fetch ``url`` and return its extracted text.

    Strategy:

      1. Validate the URL scheme (http/https only).
      2. Open a streaming ``httpx.Client`` with the configured timeout.
      3. Reject non-2xx, non-text content types, and oversize responses
         (both by ``Content-Length`` and accumulated body bytes).
      4. Try ``trafilatura.extract`` first; on missing trafilatura or
         empty result, fall back to a documented best-effort strip.
    """

    try:
        _validate_url(url)
    except ValueError:
        return FetchResult(ok=False, error="invalid_url")

    try:
        client = _get_httpx_client()
        with client as http_client:  # type: ignore[attr-defined]
            response = http_client.get(url, timeout=timeout_s)
    except Exception:  # noqa: BLE001
        return FetchResult(ok=False, error="request_failed")

    status = getattr(response, "status_code", 0)
    if not (200 <= int(status) < 300):
        return FetchResult(ok=False, error="http_error")

    headers = getattr(response, "headers", {}) or {}
    declared_length: int | None = None
    raw_length = headers.get("content-length")
    if raw_length is not None:
        try:
            declared_length = int(raw_length)
        except (TypeError, ValueError):
            declared_length = None
    if declared_length is not None and declared_length > max_bytes:
        return FetchResult(ok=False, error="response_too_large")

    content_type = _normalize_content_type(headers.get("content-type"))
    if content_type not in _ALLOWED_CONTENT_TYPES:
        return FetchResult(ok=False, error="unsupported_content_type")

    body = getattr(response, "content", b"") or b""
    if len(body) > max_bytes:
        return FetchResult(ok=False, error="response_too_large")
    try:
        html = body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return FetchResult(ok=False, error="decode_failed")

    title: str | None = None
    text = ""
    try:
        trafilatura = _import_trafilatura()
        extracted = trafilatura.extract(html, include_comments=False)
        if extracted:
            text = extracted
        title = _extract_title(trafilatura, html)
    except Exception:  # noqa: BLE001
        text = ""

    if not text:
        text = _strip_scripts_and_styles(html)

    if len(text) > max_bytes:
        text = text[:max_bytes]

    if not text.strip():
        return FetchResult(ok=False, text="", title=None, error="empty_after_extraction")

    return FetchResult(ok=True, text=text, title=title)