"""SRA-902 — Dashboard "Create topic with references" UI + generate-now endpoint e2e tests.

Phase 9: pins the dashboard's "Create topic" affordance contract and the
``POST /api/v1/topics`` / ``GET /api/v1/entries/{id}/references`` happy paths
that the SRA-902 modal wires up.

The ``test_dashboard_renders_create_topic_button`` case is the red contract:
the SSR template must expose a ``data-action="create-topic"`` button so the
vanilla-JS controller can open the modal. Today the template carries no such
markup, so that assertion fails.

The remaining cases exercise the SRA-901 server contract through the same
``client`` fixture SRA-701 used; the fetcher and document extractor are
monkeypatched so the test does not touch the network or the filesystem.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from sqlmodel import select

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Local seam stubs -- mirror the pattern in tests/integration/test_phase9_topic_route.py
# ---------------------------------------------------------------------------


def _stub_fetcher_ok(
    monkeypatch: pytest.MonkeyPatch, *, text: str = "fetched body", title: str = "Stubbed"
) -> None:
    """Replace ``sra.sources.fetcher.fetch_url_text`` with a deterministic stub."""
    from sra.sources import fetcher as fetcher_module

    class _StubResult:
        def __init__(
            self,
            ok: bool,
            text: str = "",
            title: str | None = None,
            error: str | None = None,
        ) -> None:
            self.ok = ok
            self.text = text
            self.title = title
            self.error = error

    def _fake_fetch_url_text(
        url: str, *, timeout_s: float = 8.0, max_bytes: int = 2_000_000
    ) -> Any:
        return _StubResult(ok=True, text=text, title=title)

    monkeypatch.setattr(fetcher_module, "fetch_url_text", _fake_fetch_url_text)


def _stub_extractor(
    monkeypatch: pytest.MonkeyPatch, *, text: str = "extracted body"
) -> None:
    """Replace ``sra.sources.doc_extractor.extract_document_text`` with a stub."""
    from sra.sources import doc_extractor as doc_extractor_module

    class _StubResult:
        def __init__(self, ok: bool, text: str = "", error: str | None = None) -> None:
            self.ok = ok
            self.text = text
            self.error = error

    def _fake_extract(
        content: bytes, *, content_type: str, file_name: str, max_bytes: int = 10_000_000
    ) -> Any:
        if len(content) > max_bytes:
            return _StubResult(ok=False, text="", error="file_too_large")
        return _StubResult(ok=True, text=text)

    monkeypatch.setattr(doc_extractor_module, "extract_document_text", _fake_extract)


def _create_topic_payload() -> dict[str, Any]:
    """A minimal paste-only payload used by the regenerate-501 / GET-references cases."""
    return {
        "topic": "Transformer attention",
        "summary": "How attention is computed",
        "references": [
            {"kind": "paste", "title": "Lecture notes", "pasted_text": "some text"},
        ],
    }


def _stub_generation_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only inference, while keeping quiz/plan persistence real."""
    from datetime import date

    from sra.agents import interview_planner, quiz_graph
    from sra.agents.schemas import MCQDraft
    from sra.db.repository import create_quiz_set

    def _fake_quiz_generation(
        *, entry, user_id, session, question_count=10, option_count=4, **kwargs
    ):
        del question_count, kwargs
        drafts = [
            MCQDraft(
                question_text="Which process is described by the reference?",
                options=["Mitosis", "Diffusion", "Osmosis", "Combustion"][:option_count],
                correct_option_index=0,
                explanation="The supplied note describes mitosis.",
            )
        ]
        return create_quiz_set(
            entry=entry,
            user_id=user_id,
            session=session,
            generated_on=date.today(),
            question_count=1,
            option_count=option_count,
            drafts=drafts,
        )

    from sra.agents.interview_planner import InterviewOutline, InterviewOutlineItem

    outline = InterviewOutline(
        items=[
            InterviewOutlineItem(
                subtopic="Cell division",
                difficulty="medium",
                focus_prompt="Explain the stages.",
                source_passage_quote="Mitosis divides one cell into two daughter cells during cell division.",
                ideal_answer_rubric="Names and orders the stages.",
                bloom_level="understand",
            )
        ]
    )

    monkeypatch.setattr(quiz_graph, "generate_and_persist_questions", _fake_quiz_generation)
    monkeypatch.setattr(
        interview_planner,
        "generate_interview_outline",
        lambda entry, target_questions: outline,
    )


def _assert_usable_generation_rows(session, payload: dict[str, Any]) -> None:
    """The response IDs must resolve to a plan attached to the new quiz."""
    from sra.db.models import InterviewPlan, QuizSet

    quiz_set = session.get(QuizSet, payload["quiz_set_id"])
    plan = session.get(InterviewPlan, payload["interview_plan_id"])
    assert quiz_set is not None
    assert quiz_set.entry_id == payload["entry"]["id"]
    assert plan is not None
    assert plan.quiz_set_id == quiz_set.id


# ---------------------------------------------------------------------------
# Dashboard SSR contract -- RED for SRA-902
# ---------------------------------------------------------------------------


def test_dashboard_renders_create_topic_button(client):
    """The dashboard SSR template must expose a 'Create topic' button.

    The SRA-902 modal controller binds to ``data-action="create-topic"``;
    until the template carries that button the controller has nothing to wire
    up, so this test fails today.
    """
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.text
    assert 'data-action="create-topic"' in body, (
        "Expected dashboard template to carry a data-action='create-topic' button "
        "so the vanilla-JS controller can open the create-topic modal. "
        f"Got body length {len(body)}."
    )
    assert "Create topic" in body, (
        "Expected dashboard template to render human-visible 'Create topic' text."
    )


# ---------------------------------------------------------------------------
# POST /api/v1/topics happy paths -- GREEN today (SRA-901 shipped these)
# ---------------------------------------------------------------------------


def test_post_topics_paste_reference_only_succeeds(
    client, session, seeded_user, monkeypatch
):
    """The trivial happy path: a single paste reference returns 201 with the entry."""
    _stub_extractor(monkeypatch)
    _stub_generation_success(monkeypatch)
    response = client.post("/api/v1/topics", json=_create_topic_payload())
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["entry"]["topic"] == "Transformer attention"
    assert payload["entry"]["summary"] == "How attention is computed"
    assert isinstance(payload["quiz_set_id"], int)
    assert isinstance(payload["interview_plan_id"], int)
    _assert_usable_generation_rows(session, payload)
    assert payload["errors"] == []
    assert len(payload["references"]) == 1
    assert payload["references"][0]["kind"] == "paste"
    assert payload["references"][0]["extracted_text"] == "some text"


def test_post_topics_full_flow_persists_entry_with_three_reference_kinds(
    client, session, seeded_user, monkeypatch
):
    """POST /api/v1/topics with paste + url + upload persists all three kinds.

    Each row's ``extracted_text`` is populated from the appropriate seam
    (paste mirrors ``pasted_text``; url comes from the fetcher stub; upload
    comes from the extractor stub). Deterministic inference seams require
    usable quiz and interview IDs from the same create request.
    """
    _stub_fetcher_ok(monkeypatch, text="fetched body")
    _stub_extractor(monkeypatch, text="extracted body")
    _stub_generation_success(monkeypatch)

    body = {
        "topic": "Transformer attention heads",
        "summary": "How attention is computed and why multi-head works",
        "references": [
            {"kind": "paste", "title": "Lecture notes", "pasted_text": "some text"},
            {
                "kind": "url",
                "title": "Illustrated transformer",
                "url": "https://example.com/illustrated-transformer",
            },
            {
                "kind": "upload",
                "title": "Chapter 3",
                "file_name": "chapter3.txt",
                "content_type": "text/plain",
                "file_b64": base64.b64encode(b"hello\nworld").decode("ascii"),
            },
        ],
    }
    response = client.post("/api/v1/topics", json=body)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["entry"]["topic"] == "Transformer attention heads"
    assert isinstance(payload["quiz_set_id"], int)
    assert isinstance(payload["interview_plan_id"], int)
    _assert_usable_generation_rows(session, payload)
    assert payload["errors"] == []
    assert len(payload["references"]) == 3
    by_kind = {row["kind"]: row for row in payload["references"]}
    assert by_kind["paste"]["extracted_text"] == "some text"
    assert by_kind["url"]["extracted_text"] == "fetched body"
    assert by_kind["upload"]["extracted_text"] == "extracted body"
    assert by_kind["url"]["fetch_status"] == "fetched"
    assert by_kind["upload"]["fetch_status"] == "fetched"


# ---------------------------------------------------------------------------
# 422 on invalid base64 upload payload -- RED today
# ---------------------------------------------------------------------------


def test_post_topics_upload_with_invalid_base64_returns_422(
    client, session, seeded_user, monkeypatch
):
    """``file_b64`` that is not valid base64 must surface as 422 before any DB write.

    The SRA-901 handler currently catches the base64 decode error and returns
    201 with a failed reference (partial success). SRA-902 must tighten the
    boundary so a malformed payload is a client error (422), not a silent
    success with a failed row -- the dashboard should never show a card the
    user did not actually request to create.
    """
    body = {
        "topic": "Topic",
        "summary": "Summary",
        "references": [
            {
                "kind": "upload",
                "title": "Bad file",
                "file_name": "bad.txt",
                "content_type": "text/plain",
                "file_b64": "!@#$%^&*",
            },
        ],
    }
    response = client.post("/api/v1/topics", json=body)
    assert response.status_code == 422, response.text

    from sra.db.models import ReferenceMaterial, StudyEntry

    assert session.exec(select(StudyEntry).where(StudyEntry.topic == "Topic")).first() is None
    assert session.exec(select(ReferenceMaterial)).all() == []


# ---------------------------------------------------------------------------
# Regenerate route -- 501 stub today (pins the contract for SRA-903 / SRA-904)
# ---------------------------------------------------------------------------


def test_regenerate_route_returns_200_after_phase_9_impl(
    client, session, seeded_user, monkeypatch
):
    """POST /api/v1/topics/{entry_id}/regenerate now returns 200 once SRA-903/904 land.

    The OpenAI key is ``sk-test`` under the test ``client`` fixture, so a
    real LLM call would raise immediately. The route's contract is
    partial-success: even when the LLM fails it returns 200 with
    ``generation_errors`` populated, so the dashboard can render
    "Regenerate failed" without a 5xx surprise. This test pins that
    contract.
    """
    create_response = client.post("/api/v1/topics", json=_create_topic_payload())
    assert create_response.status_code == 201
    entry_id = create_response.json()["entry"]["id"]

    response = client.post(f"/api/v1/topics/{entry_id}/regenerate")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "quiz_set_id" in payload
    assert "interview_plan_id" in payload
    assert "generation_errors" in payload


# ---------------------------------------------------------------------------
# GET /api/v1/entries/{id}/references -- GREEN today (SRA-901 shipped these)
# ---------------------------------------------------------------------------


def test_get_references_for_invalid_entry_id_returns_404(client, session, seeded_user):
    """GET /api/v1/entries/999999/references returns 404 for a missing entry."""
    response = client.get("/api/v1/entries/999999/references")
    assert response.status_code == 404


def test_get_references_for_owned_entry_returns_list(
    client, session, seeded_user, monkeypatch
):
    """GET /api/v1/entries/{id}/references returns 1 row for the owning user."""
    _stub_fetcher_ok(monkeypatch)
    create_response = client.post("/api/v1/topics", json=_create_topic_payload())
    assert create_response.status_code == 201
    entry_id = create_response.json()["entry"]["id"]

    response = client.get(f"/api/v1/entries/{entry_id}/references")
    assert response.status_code == 200, response.text
    refs = response.json()
    assert len(refs) == 1
    assert refs[0]["title"] == "Lecture notes"
    assert refs[0]["kind"] == "paste"
    assert refs[0]["extracted_text"] == "some text"
    assert refs[0]["fetch_status"] == "fetched"
