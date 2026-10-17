
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — ``POST /api/v1/topics`` end-to-end contracts through the real FastAPI app.

The fetcher is monkeypatched (no live network), the document extractor is
monkeypatched, and the route must satisfy the partial-success contract:
a single failed reference does not fail the whole request.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.models import User
from sra.db.session import get_session

pytestmark = pytest.mark.phase9

SECRET = "phase9-route-signing-secret-with-entropy"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _credential(user: User) -> str:
    assert user.id is not None
    return issue_app_credential(
        user.id, signing_secret=SECRET, ttl_seconds=600, now=datetime.now(UTC)
    )


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_credential(user)}"}


@pytest.fixture
def auth_client(client, monkeypatch):
    """A TestClient whose bearer credential uses the Phase 9 signing secret."""
    client.headers.pop("Authorization", None)
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    get_settings.cache_clear()
    yield client
    get_settings.cache_clear()


def _stub_fetcher_ok(monkeypatch: pytest.MonkeyPatch, *, text: str = "fetched body", title: str = "Stubbed") -> None:
    """Replace ``sra.sources.fetcher.fetch_url_text`` with a deterministic stub."""
    from sra.sources import fetcher as fetcher_module

    class _StubResult:
        def __init__(self, ok: bool, text: str = "", title: str | None = None, error: str | None = None) -> None:
            self.ok = ok
            self.text = text
            self.title = title
            self.error = error

    def _fake_fetch_url_text(url: str, *, timeout_s: float = 8.0, max_bytes: int = 2_000_000) -> Any:
        return _StubResult(ok=True, text=text, title=title)

    monkeypatch.setattr(fetcher_module, "fetch_url_text", _fake_fetch_url_text)


def _stub_fetcher_failure(monkeypatch: pytest.MonkeyPatch, *, error: str = "http_error") -> None:
    from sra.sources import fetcher as fetcher_module

    class _StubResult:
        def __init__(self, ok: bool, text: str = "", title: str | None = None, error: str | None = None) -> None:
            self.ok = ok
            self.text = text
            self.title = title
            self.error = error

    def _fake_fetch_url_text(url: str, *, timeout_s: float = 8.0, max_bytes: int = 2_000_000) -> Any:
        return _StubResult(ok=False, text="", title=None, error=error)

    monkeypatch.setattr(fetcher_module, "fetch_url_text", _fake_fetch_url_text)


def _stub_extractor(monkeypatch: pytest.MonkeyPatch, *, text: str = "extracted body") -> None:
    from sra.sources import doc_extractor as doc_extractor_module

    class _StubResult:
        def __init__(self, ok: bool, text: str = "", error: str | None = None) -> None:
            self.ok = ok
            self.text = text
            self.error = error

    def _fake_extract(content: bytes, *, content_type: str, file_name: str, max_bytes: int = 10_000_000) -> Any:
        # Mirror the real function's size cap so the route's pre-check
        # agrees with the extractor's behavior on small payloads.
        if len(content) > max_bytes:
            return _StubResult(ok=False, text="", error="file_too_large")
        return _StubResult(ok=True, text=text)

    monkeypatch.setattr(doc_extractor_module, "extract_document_text", _fake_extract)


def _patch_router_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the module-level seams the production topics router is expected to use.

    The implementation must import ``sra.sources.fetcher.fetch_url_text`` and
    ``sra.sources.doc_extractor.extract_document_text`` from their source
    modules so this test (which patches those source modules) drives the
    route's behavior. If the implementer imports the callables via
    ``from sra.sources.fetcher import fetch_url_text`` instead, the test must
    be updated to patch the topics router module's bound name.

    No-op today: the production sources package does not exist yet. The real
    patching happens in ``_stub_fetcher_ok`` / ``_stub_extractor`` above, which
    rebind the source-module attributes the route must look up at call time.
    """
    return None


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_post_topics_with_url_and_paste_returns_201_and_populates_extracted_text(
    auth_client, session, seeded_user, monkeypatch
):
    _patch_router_dependencies(monkeypatch)
    _stub_fetcher_ok(monkeypatch, text="some text")

    body = {
        "topic": "Transformer attention heads",
        "summary": "How attention is computed",
        "references": [
            {"kind": "paste", "title": "Lecture notes", "pasted_text": "some text"},
            {"kind": "url", "title": "Illustrated transformer", "url": "https://example.com/article"},
        ],
    }

    response = auth_client.post("/api/v1/topics", json=body, headers=_headers(seeded_user))

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["entry"]["topic"] == "Transformer attention heads"
    # This SRA-901-focused test deliberately leaves inference unstubbed. The
    # Phase 9 create route now attempts Generate now in the same request, so
    # the invalid test key produces an explicit partial-generation error while
    # reference ingestion still succeeds.
    assert payload["quiz_set_id"] is None
    assert payload["interview_plan_id"] is None
    assert any(error.startswith("quiz:") for error in payload["errors"])
    assert len(payload["references"]) == 2
    by_kind = {row["kind"]: row for row in payload["references"]}
    assert by_kind["paste"]["extracted_text"] == "some text"
    assert by_kind["url"]["extracted_text"] == "some text"


def test_post_topics_with_upload_returns_201_with_extracted_text(
    auth_client, session, seeded_user, monkeypatch
):
    _patch_router_dependencies(monkeypatch)
    _stub_extractor(monkeypatch, text="hello\nworld")

    body = {
        "topic": "Latent Dirichlet allocation",
        "summary": "Topic modeling primer",
        "references": [
            {
                "kind": "upload",
                "title": "Blei 2003",
                "file_name": "blei.txt",
                "content_type": "text/plain",
                "file_b64": base64.b64encode(b"hello\nworld").decode("ascii"),
            },
        ],
    }
    response = auth_client.post("/api/v1/topics", json=body, headers=_headers(seeded_user))

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["references"][0]["extracted_text"] == "hello\nworld"


# ---------------------------------------------------------------------------
# Partial-success: one bad URL does not fail the whole request
# ---------------------------------------------------------------------------


def test_post_topics_with_bad_url_yields_partial_success_201(
    auth_client, session, seeded_user, monkeypatch
):
    _patch_router_dependencies(monkeypatch)
    _stub_fetcher_failure(monkeypatch, error="http_error")

    body = {
        "topic": "Topic",
        "summary": "Summary",
        "references": [
            {"kind": "url", "title": "Bad URL", "url": "https://localhost:9/no-such-host"},
        ],
    }
    response = auth_client.post("/api/v1/topics", json=body, headers=_headers(seeded_user))

    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload["references"]) == 1
    assert payload["references"][0]["fetch_status"] == "failed"
    assert payload["references"][0]["fetch_error"] == "http_error"
    assert payload["errors"]  # at least one entry


# ---------------------------------------------------------------------------
# 422 / 401 / 403 contracts
# ---------------------------------------------------------------------------


def test_post_topics_with_kind_discriminator_violation_returns_422(
    auth_client, session, seeded_user, monkeypatch
):
    _patch_router_dependencies(monkeypatch)

    body = {
        "topic": "Topic",
        "summary": "Summary",
        "references": [
            {"kind": "url", "title": "Missing url"},  # url=None is a discriminator violation
        ],
    }
    response = auth_client.post("/api/v1/topics", json=body, headers=_headers(seeded_user))

    assert response.status_code == 422
    detail = response.json()["detail"]
    # The field name must appear somewhere in the detail (Pydantic v2 nests
    # the violation under ``loc`` and ``msg``); we just check the locator
    # mentions the references list.
    assert any("references" in (item.get("loc", []) if isinstance(item, dict) else [])
               for item in detail), detail


def test_post_topics_with_no_references_returns_422(auth_client, session, seeded_user, monkeypatch):
    _patch_router_dependencies(monkeypatch)

    body = {"topic": "Topic", "summary": "Summary", "references": []}
    response = auth_client.post("/api/v1/topics", json=body, headers=_headers(seeded_user))

    assert response.status_code == 422


def test_post_topics_without_auth_returns_401(monkeypatch):
    """A request with no credential at all must be rejected with 401 before any DB write."""

    from sra.api.app import create_app
    from sra.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SECRET)
    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: None  # type: ignore[arg-type,return-value]
    client = TestClient(app)

    body = {
        "topic": "Topic",
        "summary": "Summary",
        "references": [
            {"kind": "paste", "title": "t", "pasted_text": "x"},
        ],
    }
    response = client.post("/api/v1/topics", json=body)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Cross-user isolation
# ---------------------------------------------------------------------------


def test_post_topics_and_get_references_are_user_scoped(
    auth_client, session, seeded_user, other_user, monkeypatch
):
    _patch_router_dependencies(monkeypatch)
    _stub_fetcher_ok(monkeypatch, text="fetched body")

    body = {
        "topic": "Topic",
        "summary": "Summary",
        "references": [
            {"kind": "paste", "title": "Mine", "pasted_text": "mine"},
        ],
    }
    create_response = auth_client.post(
        "/api/v1/topics", json=body, headers=_headers(seeded_user)
    )
    assert create_response.status_code == 201
    entry_id = create_response.json()["entry"]["id"]

    mine = auth_client.get(
        f"/api/v1/entries/{entry_id}/references", headers=_headers(seeded_user)
    )
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    assert mine.json()[0]["title"] == "Mine"

    theirs = auth_client.get(
        f"/api/v1/entries/{entry_id}/references", headers=_headers(other_user)
    )
    assert theirs.status_code == 403
