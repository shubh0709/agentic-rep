
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — ``sra.sources.doc_extractor.extract_document_text`` unit contracts.

Mirrors the fetcher test layout: real round-trip through ``pypdf`` and
``python-docx`` for the happy paths, and monkeypatched seams so the heavy
modules can be replaced with deterministic fakes when needed.

Two module-level seams the production code MUST expose (per the
``quiz_graph._get_chat_model`` pattern):

  * ``sra.sources.doc_extractor._get_pdf_reader`` → returns a class
    (default: ``pypdf.PdfReader``), accepting ``io.BytesIO`` bytes.
  * ``sra.sources.doc_extractor._get_docx_document`` → returns a class
    (default: ``docx.Document``), accepting ``io.BytesIO`` bytes.

The happy-path tests import the real ``pypdf`` and ``python-docx`` in
``setup_method`` to *create* the test fixtures; they do NOT require the
real modules to be importable in the test process — the seamed accessors
make the extractor testable even when the heavy libs are missing. If
pypdf / docx are missing, the helper ``_maybe_make_pdf`` /
``_maybe_make_docx`` simply returns ``None`` and the test is skipped.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _maybe_make_pdf_bytes() -> bytes | None:
    """Build a real one-page PDF in-memory, or skip if pypdf isn't installed."""
    try:
        from pypdf import PdfWriter
    except ImportError:
        return None
    from io import BytesIO

    writer = PdfWriter()
    writer.add_blank_page(width=72 * 8, height=72 * 10)  # letter
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _maybe_make_docx_bytes() -> bytes | None:
    """Build a real one-paragraph DOCX in-memory, or skip if python-docx isn't installed."""
    try:
        import docx  # type: ignore[import-not-found]
    except ImportError:
        return None
    from io import BytesIO

    document = docx.Document()  # type: ignore[attr-defined]
    document.add_paragraph("Hello from a docx paragraph.")
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Happy paths via real round-trip
# ---------------------------------------------------------------------------


def test_extract_document_text_handles_plain_text(monkeypatch):
    from sra.sources import doc_extractor as doc_extractor_module

    result = doc_extractor_module.extract_document_text(
        b"hello\nworld",
        content_type="text/plain",
        file_name="notes.txt",
    )
    assert result.ok is True
    assert "hello" in result.text
    assert "world" in result.text
    assert result.error is None


def test_extract_document_text_handles_markdown_via_content_type(monkeypatch):
    from sra.sources import doc_extractor as doc_extractor_module

    md = b"# Heading\n\nA **bold** paragraph with a list:\n\n- one\n- two\n"
    result = doc_extractor_module.extract_document_text(
        md,
        content_type="text/markdown",
        file_name="notes.md",
    )
    assert result.ok is True
    assert "Heading" in result.text
    assert "bold" in result.text
    assert "one" in result.text
    assert result.error is None


def test_extract_document_text_handles_pdf_via_real_round_trip(monkeypatch):
    """Build a real PDF in setup_method, then feed the bytes back through the extractor.

    This is the "round trip is real" requirement from the brief: the
    extractor's PDF path is exercised against bytes that pypdf can
    actually parse.
    """
    from sra.sources import doc_extractor as doc_extractor_module

    pdf_bytes = _maybe_make_pdf_bytes()
    if pdf_bytes is None:
        pytest.skip("pypdf not installed; install it to enable the PDF round-trip test")
    result = doc_extractor_module.extract_document_text(
        pdf_bytes,
        content_type="application/pdf",
        file_name="blank.pdf",
    )
    # A blank PDF returns empty text after extraction; the extractor
    # should still report ok=True (no decoder error) — but the
    # fixture also exercises the success-path return shape.
    assert result.ok is True
    assert result.error is None


def test_extract_document_text_handles_docx_via_real_round_trip():
    from sra.sources import doc_extractor as doc_extractor_module

    docx_bytes = _maybe_make_docx_bytes()
    if docx_bytes is None:
        pytest.skip("python-docx not installed; install it to enable the DOCX round-trip test")
    result = doc_extractor_module.extract_document_text(
        docx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_name="letter.docx",
    )
    assert result.ok is True
    assert "Hello from a docx paragraph" in result.text
    assert result.error is None


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_extract_document_text_rejects_payload_larger_than_max_bytes(monkeypatch):
    from sra.sources import doc_extractor as doc_extractor_module

    result = doc_extractor_module.extract_document_text(
        b"x" * 200,
        content_type="text/plain",
        file_name="big.txt",
        max_bytes=10,
    )
    assert result.ok is False
    assert result.error == "file_too_large"


def test_extract_document_text_rejects_zip_content_type(monkeypatch):
    from sra.sources import doc_extractor as doc_extractor_module

    result = doc_extractor_module.extract_document_text(
        b"PK\x03\x04not actually a zip but the right magic",
        content_type="application/zip",
        file_name="payload.zip",
    )
    assert result.ok is False
    assert result.error == "unsupported_content_type"


def test_extract_document_text_rejects_zip_extension(monkeypatch):
    """``.zip`` extension with a generic content_type must still be rejected."""
    from sra.sources import doc_extractor as doc_extractor_module

    result = doc_extractor_module.extract_document_text(
        b"junk",
        content_type="text/plain",
        file_name="payload.zip",
    )
    assert result.ok is False
    assert result.error == "unsupported_content_type"


def test_extract_document_text_rejects_empty_plain_text(monkeypatch):
    from sra.sources import doc_extractor as doc_extractor_module

    result = doc_extractor_module.extract_document_text(
        b"",
        content_type="text/plain",
        file_name="empty.txt",
    )
    assert result.ok is False
    assert result.error == "empty_after_extraction"


def test_extract_document_text_rejects_empty_text_with_only_whitespace(monkeypatch):
    from sra.sources import doc_extractor as doc_extractor_module

    result = doc_extractor_module.extract_document_text(
        b"   \n\t   ",
        content_type="text/plain",
        file_name="blank.txt",
    )
    assert result.ok is False
    assert result.error == "empty_after_extraction"


# ---------------------------------------------------------------------------
# Lazy import + seam tests
# ---------------------------------------------------------------------------


def test_importing_doc_extractor_does_not_load_pypdf_or_python_docx(monkeypatch):
    """The extractor must not eagerly import pypdf / python-docx at module top."""
    import sys

    sys.modules.pop("pypdf", None)
    sys.modules.pop("docx", None)
    monkeypatch.delitem(sys.modules, "pypdf", raising=False)
    monkeypatch.delitem(sys.modules, "docx", raising=False)

    import sra.sources.doc_extractor  # noqa: F401  -- intentional: must not raise

    assert "pypdf" not in sys.modules, "doc_extractor must not eagerly import pypdf"
    assert "docx" not in sys.modules, "doc_extractor must not eagerly import python-docx"


def test_extract_document_text_uses_pdf_seam_when_provided(monkeypatch):
    """A test-injected fake ``_get_pdf_reader`` must short-circuit pypdf.

    Proves the seam is wired correctly so the integration test can
    substitute a deterministic reader without needing the real pypdf
    module loaded.
    """
    from io import BytesIO

    from sra.sources import doc_extractor as doc_extractor_module

    class _FakePage:
        def extract_text(self) -> str:
            return "FAKE PDF PAGE TEXT"

    class _FakeReader:
        def __init__(self, stream: BytesIO) -> None:
            _ = stream.read()

        @property
        def pages(self) -> list[_FakePage]:
            return [_FakePage()]

    monkeypatch.setattr(doc_extractor_module, "_get_pdf_reader", lambda: _FakeReader)

    result = doc_extractor_module.extract_document_text(
        b"%PDF-fake-bytes",
        content_type="application/pdf",
        file_name="fake.pdf",
    )
    assert result.ok is True
    assert "FAKE PDF PAGE TEXT" in result.text


def test_extract_document_text_uses_docx_seam_when_provided(monkeypatch):
    """A test-injected fake ``_get_docx_document`` must short-circuit python-docx."""
    from sra.sources import doc_extractor as doc_extractor_module

    class _FakeParagraph:
        @property
        def text(self) -> str:
            return "FAKE DOCX PARAGRAPH"

    class _FakeDocument:
        def __init__(self, stream) -> None:  # noqa: ANN001
            _ = stream.read()

        @property
        def paragraphs(self) -> list[_FakeParagraph]:
            return [_FakeParagraph()]

    monkeypatch.setattr(doc_extractor_module, "_get_docx_document", lambda: _FakeDocument)

    result = doc_extractor_module.extract_document_text(
        b"PK\x03\x04fake-docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_name="fake.docx",
    )
    assert result.ok is True
    assert "FAKE DOCX PARAGRAPH" in result.text
