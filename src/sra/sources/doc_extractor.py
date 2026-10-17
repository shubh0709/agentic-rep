"""SRA-901 extract_document_text -- lazy import seams for pypdf + python-docx."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExtractResult:
    """Outcome of one document extraction attempt.

    ``ok=True`` means ``text`` carries the extracted string; ``ok=False``
    means ``error`` is one of the documented error codes:
    ``file_too_large``, ``unsupported_content_type``,
    ``empty_after_extraction``, ``decode_failed``.
    """

    ok: bool
    text: str = ""
    error: str | None = None


_ALLOWED_CONTENT_TYPES = (
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

_ALLOWED_EXTENSIONS = (".txt", ".md", ".pdf", ".docx")

_EXTENSION_TO_KIND = {
    ".txt": "text",
    ".md": "text",
    ".pdf": "pdf",
    ".docx": "docx",
}


def _get_pdf_reader():
    """Lazy pypdf loader; tests substitute a fake class via this seam."""

    import pypdf  # type: ignore[import-not-found]

    return pypdf.PdfReader


def _get_docx_document():
    """Lazy python-docx loader; tests substitute a fake class via this seam."""

    import docx  # type: ignore[import-not-found]

    return docx.Document


def _resolve_kind(*, content_type: str, file_name: str) -> str | None:
    """Return one of ``text``, ``pdf``, ``docx`` or ``None`` for unsupported inputs.

    The file extension is authoritative when present: ``.zip`` (or any
    other unrecognized extension) is rejected even when the caller
    mislabels the upload as ``text/plain``. When no extension is
    present, fall back to the content type.
    """

    normalized_ct = (content_type or "").split(";", 1)[0].strip().lower()
    lowered_name = (file_name or "").strip().lower()

    extension: str | None = None
    if "." in lowered_name:
        extension = "." + lowered_name.rsplit(".", 1)[-1]

    if extension is not None:
        if extension in _EXTENSION_TO_KIND:
            return _EXTENSION_TO_KIND[extension]
        return None

    if normalized_ct in ("text/plain", "text/markdown"):
        return "text"
    if normalized_ct == "application/pdf":
        return "pdf"
    if normalized_ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "docx"
    return None


def extract_document_text(
    content: bytes,
    *,
    content_type: str,
    file_name: str,
    max_bytes: int = 10_000_000,
) -> ExtractResult:
    """Extract ``content`` according to its content-type / extension.

    Returns ``ExtractResult(ok=False, error="file_too_large")`` when
    ``content`` exceeds ``max_bytes`` before any extractor runs, and
    ``ok=False, error="unsupported_content_type"`` when neither the
    content-type nor the extension is in the allow-list. Decoding
    failures (malformed bytes) surface as ``decode_failed``.
    """

    if len(content) > max_bytes:
        return ExtractResult(ok=False, error="file_too_large")

    kind = _resolve_kind(content_type=content_type, file_name=file_name)
    if kind is None:
        return ExtractResult(ok=False, error="unsupported_content_type")

    if kind == "text":
        try:
            text = content.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, LookupError):
            return ExtractResult(ok=False, error="decode_failed")
        if not text.strip():
            return ExtractResult(ok=False, error="empty_after_extraction")
        return ExtractResult(ok=True, text=text)

    if kind == "pdf":
        from io import BytesIO

        try:
            reader_cls = _get_pdf_reader()
            reader = reader_cls(BytesIO(content))
        except Exception:  # noqa: BLE001
            return ExtractResult(ok=False, error="decode_failed")
        try:
            page_texts = [page.extract_text() or "" for page in reader.pages]
        except Exception:  # noqa: BLE001
            return ExtractResult(ok=False, error="decode_failed")
        # PDFs that parse cleanly but contain no extractable text (blank
        # pages, image-only scans) still count as ``ok=True``: the
        # decoder did its job, the page simply had nothing to give.
        return ExtractResult(ok=True, text="\n\n".join(page_texts))

    if kind == "docx":
        from io import BytesIO

        try:
            document_cls = _get_docx_document()
            document = document_cls(BytesIO(content))
        except Exception:  # noqa: BLE001
            return ExtractResult(ok=False, error="decode_failed")
        try:
            paragraph_texts = [paragraph.text for paragraph in document.paragraphs]
        except Exception:  # noqa: BLE001
            return ExtractResult(ok=False, error="decode_failed")
        joined = "\n".join(p for p in paragraph_texts if p).strip()
        if not joined:
            return ExtractResult(ok=False, error="empty_after_extraction")
        return ExtractResult(ok=True, text=joined)

    return ExtractResult(ok=False, error="unsupported_content_type")