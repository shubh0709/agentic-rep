
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — ``sra.api.schemas`` Phase 9 DTO contracts.

The Phase 9 endpoint shape (see ``docs/plans/2026-10-16-phase9-topic-authoring-and-quality.md``
section C):

  * ``TopicCreate`` — request body. ``extra="forbid"``. ``references`` is
    a list of ``ReferenceCreate`` discriminated by ``kind``.
  * ``ReferenceCreate`` — single model with ``kind`` discriminator, OR
    three sub-models wired through Pydantic's discriminated-union API.
    Either shape must reject the wrong fields per kind.
  * ``TopicCreateResult`` — response body carrying ``EntryOut``,
    ``list[ReferenceOut]``, ``quiz_set_id | None``,
    ``interview_plan_id | None``, and ``errors: list[str]``. Must
    round-trip via ``model_validate(model_dump())``.

All these symbols are not-yet-existing; the tests fail with ImportError
until SRA-901 lands.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# TopicCreate
# ---------------------------------------------------------------------------


def _schemas_module():
    from sra.api import schemas

    return schemas


def test_topic_create_rejects_extra_fields():
    schemas = _schemas_module()
    body = {
        "topic": "x",
        "summary": "s",
        "references": [
            {"kind": "paste", "title": "t", "pasted_text": "p"},
        ],
    }
    body["extra_field"] = "nope"
    with pytest.raises(ValidationError):
        schemas.TopicCreate.model_validate(body)


def test_topic_create_requires_at_least_one_reference():
    schemas = _schemas_module()
    with pytest.raises(ValidationError):
        schemas.TopicCreate.model_validate(
            {"topic": "x", "summary": "s", "references": []}
        )


def test_topic_create_accepts_paste_reference_payload():
    schemas = _schemas_module()
    body = {
        "topic": "Transformer attention",
        "summary": "How attention works",
        "references": [
            {"kind": "paste", "title": "Lecture", "pasted_text": "some text"},
        ],
    }
    obj = schemas.TopicCreate.model_validate(body)
    assert obj.topic == "Transformer attention"
    assert obj.summary == "How attention works"
    assert len(obj.references) == 1
    assert obj.references[0].kind == "paste"


def test_topic_create_accepts_url_reference_payload():
    schemas = _schemas_module()
    body = {
        "topic": "Topic",
        "summary": "Summary",
        "references": [
            {"kind": "url", "title": "Blog", "url": "https://example.com/post"},
        ],
    }
    obj = schemas.TopicCreate.model_validate(body)
    assert obj.references[0].kind == "url"
    assert obj.references[0].url == "https://example.com/post"


def test_topic_create_accepts_upload_reference_payload():
    schemas = _schemas_module()
    body = {
        "topic": "Topic",
        "summary": "Summary",
        "references": [
            {
                "kind": "upload",
                "title": "PDF",
                "file_name": "doc.pdf",
                "content_type": "application/pdf",
                "file_b64": "aGVsbG8=",
            },
        ],
    }
    obj = schemas.TopicCreate.model_validate(body)
    assert obj.references[0].kind == "upload"
    assert obj.references[0].file_name == "doc.pdf"


# ---------------------------------------------------------------------------
# ReferenceCreate discriminator
# ---------------------------------------------------------------------------


def test_reference_create_url_rejects_upload_only_fields():
    schemas = _schemas_module()
    with pytest.raises(ValidationError):
        schemas.ReferenceCreate.model_validate(
            {
                "kind": "url",
                "title": "t",
                "url": "https://example.com",
                "pasted_text": "extra",
            }
        )


def test_reference_create_paste_rejects_url_only_fields():
    schemas = _schemas_module()
    with pytest.raises(ValidationError):
        schemas.ReferenceCreate.model_validate(
            {
                "kind": "paste",
                "title": "t",
                "pasted_text": "x",
                "url": "https://example.com",
            }
        )


def test_reference_create_upload_rejects_paste_only_fields():
    schemas = _schemas_module()
    with pytest.raises(ValidationError):
        schemas.ReferenceCreate.model_validate(
            {
                "kind": "upload",
                "title": "t",
                "file_name": "f.txt",
                "content_type": "text/plain",
                "file_b64": "aGk=",
                "pasted_text": "shouldn't be here",
            }
        )


def test_reference_create_kind_must_be_a_known_literal():
    schemas = _schemas_module()
    with pytest.raises(ValidationError):
        schemas.ReferenceCreate.model_validate(
            {"kind": "bogus", "title": "t", "pasted_text": "x"}
        )


def test_reference_create_paste_requires_non_empty_pasted_text():
    schemas = _schemas_module()
    with pytest.raises(ValidationError):
        schemas.ReferenceCreate.model_validate(
            {"kind": "paste", "title": "t", "pasted_text": ""}
        )


def test_reference_create_upload_requires_file_name_content_type_file_b64():
    schemas = _schemas_module()
    for missing_field in ("file_name", "content_type", "file_b64"):
        body = {
            "kind": "upload",
            "title": "t",
            "file_name": "f.pdf",
            "content_type": "application/pdf",
            "file_b64": "aGk=",
        }
        body.pop(missing_field)
        with pytest.raises(ValidationError):
            schemas.ReferenceCreate.model_validate(body)


# ---------------------------------------------------------------------------
# TopicCreateResult
# ---------------------------------------------------------------------------


def test_topic_create_result_carries_required_fields():
    """TopicCreateResult must include entry / references / quiz_set_id / interview_plan_id / errors."""
    schemas = _schemas_module()
    # We don't construct a real EntryOut from a model because TopicCreateResult
    # is a response DTO; using a plain dict-shaped payload proves the field set.
    from datetime import UTC, datetime

    entry = schemas.EntryOut(
        id=1,
        topic="Topic",
        subtopic=None,
        tags=[],
        summary="s",
        raw_transcript="t",
        created_at=datetime.now(UTC),
    )
    obj = schemas.TopicCreateResult(
        entry=entry,
        references=[],
        quiz_set_id=None,
        interview_plan_id=None,
        errors=[],
    )
    assert obj.entry.id == 1
    assert obj.references == []
    assert obj.quiz_set_id is None
    assert obj.interview_plan_id is None
    assert obj.errors == []


def test_topic_create_result_round_trips_through_model_dump():
    schemas = _schemas_module()
    from datetime import UTC, datetime

    entry = schemas.EntryOut(
        id=42,
        topic="T",
        subtopic="S",
        tags=["x"],
        summary="sum",
        raw_transcript="raw",
        created_at=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
    )
    original = schemas.TopicCreateResult(
        entry=entry,
        references=[],
        quiz_set_id=99,
        interview_plan_id=100,
        errors=["warn-1"],
    )
    payload = original.model_dump()
    restored = schemas.TopicCreateResult.model_validate(payload)
    assert restored.entry.id == 42
    assert restored.quiz_set_id == 99
    assert restored.interview_plan_id == 100
    assert restored.errors == ["warn-1"]


def test_topic_create_result_errors_defaults_to_empty_list():
    """A successful call must surface ``errors=[]`` rather than requiring the field."""
    schemas = _schemas_module()
    from datetime import UTC, datetime

    entry = schemas.EntryOut(
        id=1,
        topic="T",
        subtopic=None,
        tags=[],
        summary="s",
        raw_transcript="t",
        created_at=datetime.now(UTC),
    )
    obj = schemas.TopicCreateResult(entry=entry, references=[], quiz_set_id=None, interview_plan_id=None)
    assert obj.errors == []
