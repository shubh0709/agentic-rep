
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 reference-material SQLModel + Pydantic discriminator contracts.

The migration is owned by SRA-901 step 3; this module pins the *Python-side*
contract the implementer must satisfy so the repository tests below know what
to import. Every test is expected to fail with ``ImportError`` today because
``ReferenceMaterial`` / ``ReferenceMaterialBase`` / ``ReferenceMaterialCreate``
do not yet exist on ``sra.db.models``.
"""

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Helpers used by every test. Mirrors the make_*_payload pattern in
# tests/unit/test_quiz_schemas.py so callers don't repeat discriminator shape.
# ---------------------------------------------------------------------------


def _import_models():
    """Lazily import the not-yet-existing models so the test file collects."""
    from sra.db import models

    return models


def make_url_reference_payload(*, title: str = "A URL", url: str = "https://example.com") -> dict:
    return {"kind": "url", "title": title, "url": url}


def make_paste_reference_payload(*, title: str = "Paste", pasted_text: str = "hello") -> dict:
    return {"kind": "paste", "title": title, "pasted_text": pasted_text}


def make_upload_reference_payload(
    *,
    title: str = "An upload",
    file_name: str = "doc.txt",
    content_type: str = "text/plain",
    file_b64: str = "aGVsbG8=",
) -> dict:
    import base64

    if not file_b64:
        file_b64 = base64.b64encode(b"x").decode("ascii")
    return {
        "kind": "upload",
        "title": title,
        "file_name": file_name,
        "content_type": content_type,
        "file_b64": file_b64,
    }


# ---------------------------------------------------------------------------
# Base + Create honor extra="forbid".
# ---------------------------------------------------------------------------


def test_reference_material_create_rejects_extra_fields():
    models = _import_models()
    payload = make_paste_reference_payload()
    payload["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        models.ReferenceMaterialCreate.model_validate(payload)


def test_reference_material_create_accepts_well_formed_paste_payload():
    models = _import_models()
    obj = models.ReferenceMaterialCreate.model_validate(make_paste_reference_payload())
    assert obj.kind == "paste"
    assert obj.title == "Paste"
    assert obj.pasted_text == "hello"


def test_reference_material_create_accepts_well_formed_url_payload():
    models = _import_models()
    obj = models.ReferenceMaterialCreate.model_validate(make_url_reference_payload())
    assert obj.kind == "url"
    assert obj.url == "https://example.com"


def test_reference_material_create_accepts_well_formed_upload_payload():
    models = _import_models()
    obj = models.ReferenceMaterialCreate.model_validate(make_upload_reference_payload())
    assert obj.kind == "upload"
    assert obj.file_name == "doc.txt"
    assert obj.content_type == "text/plain"


# ---------------------------------------------------------------------------
# Discriminator literal: kind must be one of {"url", "paste", "upload"}.
# ---------------------------------------------------------------------------


def test_reference_material_create_kind_must_be_one_of_the_three_literals():
    models = _import_models()
    bad = make_paste_reference_payload()
    bad["kind"] = "web"  # type: ignore[assignment]
    with pytest.raises(ValidationError):
        models.ReferenceMaterialCreate.model_validate(bad)


def test_reference_material_create_kind_literal_uses_exact_string_values():
    """Pin the exact Literal values used downstream by repository + fetcher.

    A refactor that renames "paste" to "pasted" or "url" to "link" must
    surface here so callers (the route, the extractor, the fetcher) and
    the discriminator stay in lock-step.
    """
    models = _import_models()
    for kind in ("url", "paste", "upload"):
        payload = {
            "url": make_url_reference_payload,
            "paste": make_paste_reference_payload,
            "upload": make_upload_reference_payload,
        }[kind]()
        obj = models.ReferenceMaterialCreate.model_validate(payload)
        assert obj.kind == kind


# ---------------------------------------------------------------------------
# Per-kind required-field validation.
# ---------------------------------------------------------------------------


def test_paste_reference_with_empty_pasted_text_is_rejected():
    models = _import_models()
    with pytest.raises(ValidationError):
        models.ReferenceMaterialCreate.model_validate(
            make_paste_reference_payload(pasted_text="")
        )


def test_paste_reference_with_whitespace_only_pasted_text_is_rejected():
    models = _import_models()
    with pytest.raises(ValidationError):
        models.ReferenceMaterialCreate.model_validate(
            make_paste_reference_payload(pasted_text="   \n  \t")
        )


def test_url_reference_with_non_http_scheme_is_rejected():
    models = _import_models()
    for bad_url in ("ftp://example.com", "javascript:alert(1)", "file:///etc/passwd"):
        with pytest.raises(ValidationError):
            models.ReferenceMaterialCreate.model_validate(
                make_url_reference_payload(url=bad_url)
            )


def test_url_reference_without_url_is_rejected():
    models = _import_models()
    payload = make_url_reference_payload()
    payload.pop("url")
    with pytest.raises(ValidationError):
        models.ReferenceMaterialCreate.model_validate(payload)


def test_upload_reference_with_disallowed_content_type_is_rejected():
    models = _import_models()
    for bad_ct in ("application/zip", "image/png", "text/csv", "application/octet-stream"):
        with pytest.raises(ValidationError):
            models.ReferenceMaterialCreate.model_validate(
                make_upload_reference_payload(content_type=bad_ct)
            )


def test_upload_reference_without_file_b64_is_rejected():
    models = _import_models()
    payload = make_upload_reference_payload()
    payload.pop("file_b64")
    with pytest.raises(ValidationError):
        models.ReferenceMaterialCreate.model_validate(payload)


# ---------------------------------------------------------------------------
# Storage model: ReferenceMaterial carries server-owned fields and the
# 3-way CHECK constraint lives on the SQLAlchemy side.
# ---------------------------------------------------------------------------


def test_reference_material_table_has_expected_check_constraint_name():
    """The CHECK constraint must be named for the migration replay test to find it.

    Downgrade logic and the integration test both reference the constraint by
    name; an unnamed constraint would silently slip past the drop.
    """
    models = _import_models()
    constraint_names = {
        c.name
        for c in models.ReferenceMaterial.__table__.constraints
        if getattr(c, "name", None) is not None
    }
    assert "ck_reference_materials_kind" in constraint_names


def test_reference_material_table_indexes_entry_id():
    models = _import_models()
    indexes = {tuple(idx.columns.keys()) for idx in models.ReferenceMaterial.__table__.indexes}
    assert ("entry_id",) in indexes


def test_reference_material_table_chains_entry_id_to_study_entries():
    models = _import_models()
    fk_targets = {
        fk.target_fullname
        for fk in models.ReferenceMaterial.__table__.foreign_keys  # type: ignore[attr-defined]
    }
    assert "study_entries.id" in fk_targets
