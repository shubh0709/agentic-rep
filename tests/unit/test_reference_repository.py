
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — ``sra.db.repository`` reference-material helper contracts.

Every test in this module is expected to fail with
``NotImplementedError`` today because the repository helpers are not yet
written. Once SRA-901 lands, the helpers must satisfy the typed-insert
discipline of ``create_entry`` (see ``src/sra/db/repository.py``).
"""

from __future__ import annotations

import pytest
from sqlmodel import select

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_repo():
    from sra.db import repository

    return repository


def _make_entry(session, seeded_user) -> int:
    from sra.db.models import StudyEntry, StudyEntryCreate

    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Topic", summary="s", raw_transcript="t"),
        update={"user_id": seeded_user.id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    return entry.id


# ---------------------------------------------------------------------------
# add_reference
# ---------------------------------------------------------------------------


def test_add_reference_persists_url_kind_with_extracted_text(session, seeded_user):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    row = repo.add_reference(
        entry_id=entry_id,
        kind="url",
        title="Illustrated transformer",
        url="https://example.com/article",
        extracted_text="article body",
        fetch_status="fetched",
        session=session,
    )

    assert row.id is not None
    assert row.entry_id == entry_id
    assert row.kind == "url"
    assert row.title == "Illustrated transformer"
    assert row.url == "https://example.com/article"
    assert row.extracted_text == "article body"
    assert row.fetch_status == "fetched"
    assert row.created_at is not None


def test_add_reference_persists_paste_kind_with_pasted_text(session, seeded_user):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    row = repo.add_reference(
        entry_id=entry_id,
        kind="paste",
        title="Lecture notes",
        pasted_text="some long text",
        session=session,
    )

    assert row.id is not None
    assert row.kind == "paste"
    assert row.pasted_text == "some long text"
    assert row.url is None
    assert row.file_name is None
    assert row.content_type is None


def test_add_reference_persists_upload_kind_with_file_metadata(session, seeded_user):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    row = repo.add_reference(
        entry_id=entry_id,
        kind="upload",
        title="Blei 2003",
        file_name="blei.pdf",
        content_type="application/pdf",
        extracted_text="LDA paper body",
        fetch_status="fetched",
        session=session,
    )

    assert row.id is not None
    assert row.kind == "upload"
    assert row.file_name == "blei.pdf"
    assert row.content_type == "application/pdf"
    assert row.extracted_text == "LDA paper body"


def test_add_reference_raises_on_kind_discriminator_violation(session, seeded_user):
    """``kind='url'`` with no ``url`` must fail before the row is persisted."""
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    with pytest.raises(ValueError):
        repo.add_reference(
            entry_id=entry_id,
            kind="url",
            title="missing url",
            session=session,
        )


def test_add_reference_raises_on_paste_without_pasted_text(session, seeded_user):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    with pytest.raises(ValueError):
        repo.add_reference(
            entry_id=entry_id,
            kind="paste",
            title="missing text",
            session=session,
        )


def test_add_reference_raises_on_upload_without_file_metadata(session, seeded_user):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    with pytest.raises(ValueError):
        repo.add_reference(
            entry_id=entry_id,
            kind="upload",
            title="missing file",
            content_type="application/pdf",
            session=session,
        )


def test_add_reference_does_not_persist_on_discriminator_failure(session, seeded_user):
    """A failed insert must leave the references table empty for that entry."""
    from sra.db.models import ReferenceMaterial

    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    with pytest.raises(ValueError):
        repo.add_reference(
            entry_id=entry_id,
            kind="url",
            title="missing url",
            session=session,
        )

    rows = session.exec(
        select(ReferenceMaterial).where(ReferenceMaterial.entry_id == entry_id)
    ).all()
    assert rows == []


# ---------------------------------------------------------------------------
# list_references_for_entry
# ---------------------------------------------------------------------------


def test_list_references_for_entry_returns_rows_in_created_at_ascending_order(
    session, seeded_user
):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    repo.add_reference(
        entry_id=entry_id, kind="paste", title="first", pasted_text="a", session=session
    )
    repo.add_reference(
        entry_id=entry_id, kind="paste", title="second", pasted_text="b", session=session
    )
    repo.add_reference(
        entry_id=entry_id, kind="paste", title="third", pasted_text="c", session=session
    )

    rows = repo.list_references_for_entry(entry_id, session=session)

    titles = [row.title for row in rows]
    assert titles == ["first", "second", "third"]


def test_list_references_for_entry_returns_empty_list_for_entry_with_no_refs(
    session, seeded_user
):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    rows = repo.list_references_for_entry(entry_id, session=session)

    assert rows == []


# ---------------------------------------------------------------------------
# get_reference_text_bundle
# ---------------------------------------------------------------------------


def test_get_reference_text_bundle_uses_pasted_text_for_paste_kind(session, seeded_user):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)
    repo.add_reference(
        entry_id=entry_id, kind="paste", title="Lecture notes", pasted_text="important body", session=session
    )

    bundle = repo.get_reference_text_bundle(entry_id, session=session)

    assert bundle == [("Lecture notes", "important body")]


def test_get_reference_text_bundle_uses_extracted_text_for_url_kind(session, seeded_user):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)
    repo.add_reference(
        entry_id=entry_id,
        kind="url",
        title="A URL",
        url="https://example.com",
        extracted_text="scraped body",
        session=session,
    )

    bundle = repo.get_reference_text_bundle(entry_id, session=session)

    assert bundle == [("A URL", "scraped body")]


def test_get_reference_text_bundle_truncates_per_ref_with_marker(session, seeded_user):
    """When a single reference exceeds ``max_chars_per_ref``, the marker is appended."""
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)
    long_text = "x" * 200
    repo.add_reference(
        entry_id=entry_id,
        kind="paste",
        title="Long",
        pasted_text=long_text,
        session=session,
    )

    bundle = repo.get_reference_text_bundle(
        entry_id, session=session, max_chars_per_ref=20, max_chars_total=12_000
    )

    assert len(bundle) == 1
    title, excerpt = bundle[0]
    assert title == "Long"
    # The excerpt should be 20 characters of payload plus a marker like
    # "[... truncated at 20 chars ...]"
    assert excerpt.startswith("x" * 20)
    assert "truncated" in excerpt


def test_get_reference_text_bundle_truncates_at_total_cap(session, seeded_user):
    """When the per-ref caps sum exceeds ``max_chars_total``, total is enforced first."""
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)
    repo.add_reference(
        entry_id=entry_id, kind="paste", title="A", pasted_text="a" * 100, session=session
    )
    repo.add_reference(
        entry_id=entry_id, kind="paste", title="B", pasted_text="b" * 100, session=session
    )

    bundle = repo.get_reference_text_bundle(
        entry_id, session=session, max_chars_per_ref=6_000, max_chars_total=80
    )

    total_chars = sum(len(excerpt) for _, excerpt in bundle)
    assert total_chars <= 80 + len("[... truncated at 80 chars ...]")


def test_get_reference_text_bundle_returns_empty_list_for_entry_with_no_refs(
    session, seeded_user
):
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)

    bundle = repo.get_reference_text_bundle(entry_id, session=session)

    assert bundle == []


# ---------------------------------------------------------------------------
# Truncation marker shape (pinned)
# ---------------------------------------------------------------------------


def test_truncation_marker_includes_the_exact_char_count(monkeypatch, session, seeded_user):
    """Pin the literal marker so the SRA-903 prompt can rely on a stable string."""
    repo = _import_repo()
    entry_id = _make_entry(session, seeded_user)
    repo.add_reference(
        entry_id=entry_id,
        kind="paste",
        title="Marker pin",
        pasted_text="z" * 50,
        session=session,
    )

    bundle = repo.get_reference_text_bundle(
        entry_id, session=session, max_chars_per_ref=10, max_chars_total=12_000
    )
    _, excerpt = bundle[0]
    assert excerpt == "z" * 10 + "[... truncated at 10 chars ...]"
