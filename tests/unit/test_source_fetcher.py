
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — ``sra.sources.fetcher.fetch_url_text`` unit contracts.

The fetcher must:

  * succeed on a 200 ``text/html`` payload (happy path, ``ok=True``);
  * reject non-2xx, non-text content-type, oversize, and malformed-URL inputs
    with a documented ``ok=False`` error string;
  * import ``trafilatura`` lazily inside the function so the heavy module is
    never loaded at test collection time;
  * expose ``FetchResult(ok, text='', title=None, error=None)``.

Every test patches ``httpx.Client`` via ``monkeypatch`` (no live network) and
substitutes the lazy trafilatura import with a fake that returns a fixed
text + title.

The expected failure today: ``ImportError`` because ``sra.sources.fetcher``
does not exist; after SRA-901 ships, the function must satisfy these
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Seam contract for the implementer
# ---------------------------------------------------------------------------
# Production code must define a module-level seam mirroring the
# ``quiz_graph._get_chat_model`` pattern: the production fetcher calls
# ``_import_trafilatura()`` (or similar) inside the function body; the
# tests below monkeypatch that seam to a fake. The seam name is
# documented here so the implementer knows where to place it.
#
#   # in sra/sources/fetcher.py:
#   def _import_trafilatura():
#       import trafilatura  # imported lazily so the dependency is optional at test time
#       return trafilatura
#
# Tests patch ``sra.sources.fetcher._import_trafilatura``.


@dataclass
class _StubTrafilatura:
    text: str = "Some extracted article text"
    title: str = "Stub Article Title"

    def extract(self, html: str, *, include_comments: bool = False, **kwargs: Any) -> str | None:
        return self.text


def _patch_fetcher_seams(monkeypatch: pytest.MonkeyPatch, *, trafilatura_text: str | None = "Some text") -> None:
    """Patch the module-level seams the production fetcher is expected to expose."""
    from sra.sources import fetcher as fetcher_module

    stub = _StubTrafilatura(text=trafilatura_text or "", title="Stub Article Title")
    monkeypatch.setattr(fetcher_module, "_import_trafilatura", lambda: stub)


def _make_response(
    *,
    status_code: int = 200,
    content_type: str = "text/html; charset=utf-8",
    body: bytes = b"<html><body><p>hi</p></body></html>",
    content_length: int | None = None,
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {
        "content-type": content_type,
        "content-length": str(content_length if content_length is not None else len(body)),
    }
    response.content = body
    response.text = body.decode("utf-8", errors="replace")
    return response


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, response: MagicMock) -> None:
    """Patch ``sra.sources.fetcher._get_httpx_client`` to return a context-manager mock."""
    from sra.sources import fetcher as fetcher_module

    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.get = MagicMock(return_value=response)
    monkeypatch.setattr(fetcher_module, "_get_httpx_client", lambda: client_mock)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_fetch_url_text_returns_ok_text_and_title_on_200_text_html(monkeypatch):
    from sra.sources import fetcher as fetcher_module

    _patch_fetcher_seams(monkeypatch, trafilatura_text="Some text")
    _patch_httpx_client(monkeypatch, _make_response())

    result = fetcher_module.fetch_url_text("https://example.com/article")

    assert result.ok is True
    assert result.text == "Some text"
    assert result.title == "Stub Article Title"
    assert result.error is None


def test_fetch_result_dataclass_exposes_documented_fields():
    """``FetchResult`` must carry ``ok / text / title / error`` and nothing else mandatory."""
    from sra.sources.fetcher import FetchResult

    r = FetchResult(ok=True)
    assert r.ok is True
    assert r.text == ""
    assert r.title is None
    assert r.error is None


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_fetch_url_text_returns_non_2xx_as_ok_false_with_http_error(monkeypatch):
    from sra.sources import fetcher as fetcher_module

    _patch_fetcher_seams(monkeypatch, trafilatura_text="")
    _patch_httpx_client(monkeypatch, _make_response(status_code=404))

    result = fetcher_module.fetch_url_text("https://example.com/missing")

    assert result.ok is False
    assert result.error == "http_error"
    assert result.text == ""


def test_fetch_url_text_returns_5xx_as_ok_false_with_http_error(monkeypatch):
    from sra.sources import fetcher as fetcher_module

    _patch_fetcher_seams(monkeypatch, trafilatura_text="")
    _patch_httpx_client(monkeypatch, _make_response(status_code=503))

    result = fetcher_module.fetch_url_text("https://example.com/down")

    assert result.ok is False
    assert result.error == "http_error"


def test_fetch_url_text_rejects_non_text_content_type(monkeypatch):
    from sra.sources import fetcher as fetcher_module

    _patch_fetcher_seams(monkeypatch, trafilatura_text="")
    _patch_httpx_client(
        monkeypatch,
        _make_response(content_type="application/octet-stream", body=b"\x00\x01\x02"),
    )

    result = fetcher_module.fetch_url_text("https://example.com/binary")

    assert result.ok is False
    assert result.error == "unsupported_content_type"


def test_fetch_url_text_rejects_malformed_url(monkeypatch):
    from sra.sources import fetcher as fetcher_module

    _patch_fetcher_seams(monkeypatch, trafilatura_text="")

    result = fetcher_module.fetch_url_text("not a real url at all")

    assert result.ok is False
    assert result.error == "invalid_url"


def test_fetch_url_text_rejects_oversize_content_length_header(monkeypatch):
    from sra.sources import fetcher as fetcher_module

    _patch_fetcher_seams(monkeypatch, trafilatura_text="")
    _patch_httpx_client(
        monkeypatch,
        _make_response(content_length=2_500_000, body=b"x" * 2_500_000),
    )

    result = fetcher_module.fetch_url_text("https://example.com/big", max_bytes=2_000_000)

    assert result.ok is False
    assert result.error == "response_too_large"


def test_fetch_url_text_rejects_body_exceeding_max_bytes(monkeypatch):
    """Content-Length absent / lying — the fetcher must enforce the cap on accumulated body."""
    from sra.sources import fetcher as fetcher_module

    _patch_fetcher_seams(monkeypatch, trafilatura_text="")
    _patch_httpx_client(
        monkeypatch,
        _make_response(content_length=10, body=b"x" * 2_100_000),
    )

    result = fetcher_module.fetch_url_text("https://example.com/lying", max_bytes=2_000_000)

    assert result.ok is False
    assert result.error == "response_too_large"


# ---------------------------------------------------------------------------
# Lazy import: trafilatura must not be loaded at module import time
# ---------------------------------------------------------------------------


def test_importing_fetcher_module_does_not_load_trafılatura(monkeypatch):
    """``sra.sources.fetcher`` must avoid ``import trafilatura`` at module top.

    If the production module imports trafilatura eagerly, this test fails
    with a ModuleNotFoundError because trafilatura is not yet pinned in
    pyproject.toml.
    """
    import sys

    sys.modules.pop("trafilatura", None)
    monkeypatch.delitem(sys.modules, "trafilatura", raising=False)

    import sra.sources.fetcher  # noqa: F401  -- intentional: must not raise

    assert "trafilatura" not in sys.modules, "fetcher.py must not eagerly import trafilatura"
