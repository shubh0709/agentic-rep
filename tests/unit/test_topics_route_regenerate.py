"""SRA-902 — ``POST /api/v1/topics/{entry_id}/regenerate`` route contract tests (phase9).

Phase 9: pins the regenerate route's SRA-902 contract. The route is a
``501 Not Implemented`` stub today (SRA-901 shipped it as a placeholder);
SRA-902 / SRA-903 / SRA-904 replace it with a real handler.

  * ``test_regenerate_route_returns_501_for_authenticated_user`` -- GREEN
    today, pins the 501 stub contract.
  * ``test_regenerate_route_returns_401_without_auth`` -- GREEN today
    (``require_user`` fires before the handler body).
  * ``test_regenerate_route_returns_403_for_other_users_entry`` -- RED
    today (handler returns 501 unconditionally instead of 403 after the
    cross-user check). Fails for the right reason: the handler is a stub.
  * ``test_regenerate_route_returns_404_for_missing_entry`` -- RED today
    (handler returns 501 unconditionally instead of 404 after the
    missing-entry check). Same failure mode as the 403 case.

The brief noted "the route is not mounted yet"; SRA-901 already mounted it,
so the RED cases fail with 501 (not 404). The intent is the same: the
implementation must replace the stub with real checks and a 200 response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings
from sra.db.models import User
from sra.db.session import get_session

pytestmark = pytest.mark.phase9


# Must match the signing secret the conftest ``client`` fixture uses so
# credentials issued here are accepted by the same app instance.
SIGNING_SECRET = "rate-limit-test-signing-secret-with-entropy"


def _credential(user: User) -> str:
    assert user.id is not None
    return issue_app_credential(
        user.id,
        signing_secret=SIGNING_SECRET,
        ttl_seconds=600,
        now=datetime.now(UTC),
    )


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_credential(user)}"}


def _stub_fetcher_ok(
    monkeypatch: pytest.MonkeyPatch, *, text: str = "fetched body"
) -> None:
    """Stub the URL fetcher so POST /api/v1/topics does not hit the network."""
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
        return _StubResult(ok=True, text=text, title="Stubbed")

    monkeypatch.setattr(fetcher_module, "fetch_url_text", _fake_fetch_url_text)


def _create_topic_via_api(client: TestClient) -> int:
    """POST a paste-only topic and return the new ``entry_id``."""
    response = client.post(
        "/api/v1/topics",
        json={
            "topic": "Topic",
            "summary": "Summary",
            "references": [
                {"kind": "paste", "title": "Notes", "pasted_text": "some text"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["entry"]["id"]


# ---------------------------------------------------------------------------
# GREEN today -- pins the 501 stub and the 401 auth requirement
# ---------------------------------------------------------------------------


def test_regenerate_route_returns_200_for_authenticated_user_with_successful_generation(
    client, session, seeded_user, monkeypatch
):
    """POST /api/v1/topics/{entry_id}/regenerate returns 200 once SRA-903/904 lands.

    The LLM is stubbed via a real ``_get_draft_model`` monkeypatch returning
    one valid MCQDraft, so the quiz pipeline produces a real QuizSet with
    an id. The interview pipeline is also patched to produce a real plan.
    The shape contract is::

        { quiz_set_id: int | null, interview_plan_id: int | null,
          generation_errors: [] }

    The dashboard JS inspects these fields directly. A non-null
    ``quiz_set_id`` causes "Start quiz" to render; non-null
    ``interview_plan_id`` causes "Start interview" to render; a non-empty
    ``generation_errors`` is rendered as muted text below the actions.
    """
    from sra.agents import interview_planner, quiz_graph

    class _FakeDraft:
        def __init__(self) -> None:
            self.question_text = "What does softmax(QK^T/sqrt(d_k)) represent?"
            self.options = ["Scaled dot-product attention", "Convex combination", "Layer norm", "Dropout"]
            self.correct_option_index = 0
            self.explanation = "It is the scaled dot-product attention weighting."
            self.bloom_level = "apply"
            self.source_passage_quote = "scaled dot-product attention is defined as softmax"
            self.distractor_misconception = "Conflating QK^T scaling with QK normalization."

        def model_dump(self) -> dict[str, Any]:
            return {
                "question_text": self.question_text,
                "options": list(self.options),
                "correct_option_index": self.correct_option_index,
                "explanation": self.explanation,
                "bloom_level": self.bloom_level,
                "source_passage_quote": self.source_passage_quote,
                "distractor_misconception": self.distractor_misconception,
            }

    class _FakeDraftsContainer:
        def __init__(self, drafts: list[Any]) -> None:
            self.drafts = drafts

    class _FakeVerifyVerdict:
        def __init__(self, verdict: str = "yes", reason: str = "stub") -> None:
            self.verdict = verdict
            self.reason = reason

    fake_draft = _FakeDraft()

    def _fake_draft_model() -> Any:
        return _FakeDraftsContainer([fake_draft])

    def _fake_verify_model() -> Any:
        return _FakeVerifyVerdict("yes")

    monkeypatch.setattr(quiz_graph, "_get_draft_model", _fake_draft_model)
    monkeypatch.setattr(quiz_graph, "_get_verify_model", _fake_verify_model)
    monkeypatch.setattr(quiz_graph, "_verify_gate_enabled", lambda: True)

    class _FakeOutlineItem:
        subtopic = "scaled dot-product attention"
        difficulty = "medium"
        focus_prompt = "Walk me through why we divide by sqrt(d_k)."
        source_passage_quote = "we divide by sqrt(d_k) to keep the variance stable"
        ideal_answer_rubric = "Correct answer states scaling prevents softmax saturation."
        bloom_level = "analyze"

    class _FakeOutline:
        items: list[Any] = []

    def _fake_generate_interview_outline(entry: Any, target_questions: int) -> Any:
        _FakeOutline.items = [_FakeOutlineItem() for _ in range(target_questions)]
        return _FakeOutline()

    monkeypatch.setattr(
        interview_planner, "generate_interview_outline", _fake_generate_interview_outline
    )

    entry_id = _create_topic_via_api(client)
    response = client.post(f"/api/v1/topics/{entry_id}/regenerate")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "quiz_set_id" in payload
    assert "interview_plan_id" in payload
    assert "generation_errors" in payload
    assert isinstance(payload["generation_errors"], list)


def test_regenerate_route_returns_401_without_auth(monkeypatch, session):
    """POST /api/v1/topics/{entry_id}/regenerate returns 401 without auth.

    ``require_user`` fires before the handler body, so even the 501 stub
    surfaces 401 to an unauthenticated caller. This is the correct security
    boundary today and must hold after SRA-902 / SRA-903 / SRA-904 land.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SIGNING_SECRET)
    get_settings.cache_clear()
    try:
        app = create_app()
        app.dependency_overrides[get_session] = lambda: session
        anon_client = TestClient(app)

        response = anon_client.post("/api/v1/topics/1/regenerate")
        assert response.status_code == 401, response.text
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# RED today -- handler is a 501 stub, must become real checks
# ---------------------------------------------------------------------------


def test_regenerate_route_returns_403_for_other_users_entry(
    client, session, seeded_user, other_user, monkeypatch
):
    """User B hitting ``POST /api/v1/topics/{A's entry}/regenerate`` gets 403.

    The implementation must run the cross-user check before the LLM seam
    call: load the entry, confirm ``entry.user_id == user.id``, return 403
    otherwise. Today the handler raises 501 unconditionally, so this
    assertion fails for the right reason.
    """
    _stub_fetcher_ok(monkeypatch)
    # seeded_user (the ``client`` fixture's user) creates the topic.
    entry_id = _create_topic_via_api(client)

    # other_user tries to regenerate seeded_user's entry.
    response = client.post(
        f"/api/v1/topics/{entry_id}/regenerate",
        headers=_headers(other_user),
    )
    assert response.status_code == 403, response.text


def test_regenerate_route_returns_404_for_missing_entry(
    client, session, seeded_user
):
    """POST /api/v1/topics/999999/regenerate returns 404 for a missing entry.

    The implementation must run the missing-entry check first: load the
    entry, return 404 when ``session.get(StudyEntry, entry_id) is None``.
    Today the handler raises 501 unconditionally, so this assertion fails
    for the right reason.
    """
    response = client.post("/api/v1/topics/999999/regenerate")
    assert response.status_code == 404, response.text


def test_regenerate_updates_todays_generated_artifacts_in_place(
    client, session, seeded_user, monkeypatch
):
    """Regeneration refreshes content without replacing its parent QuizSet.

    A completed quiz attempt may already point at today's QuizSet, so deleting
    that row and inserting another is not a safe implementation strategy. The
    route must replace its generated questions and interview outline in place,
    while leaving the entry's independent review schedule untouched.
    """
    from datetime import date

    from sqlmodel import select

    from sra.agents import interview_planner, quiz_graph
    from sra.agents.interview_planner import InterviewOutline, InterviewOutlineItem
    from sra.agents.schemas import MCQDraft, MCQDrafts
    from sra.db import repository
    from sra.db.models import (
        InterviewPlan,
        QuizAttempt,
        QuizQuestion,
        QuizSet,
        ReviewSchedule,
        StudyEntry,
    )

    entry_id = _create_topic_via_api(client)
    entry = session.get(StudyEntry, entry_id)
    assert entry is not None
    assert seeded_user.id is not None

    original_quiz = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=1,
        option_count=4,
        drafts=[
            MCQDraft(
                question_text="Original generated question?",
                options=["old-a", "old-b", "old-c", "old-d"],
                correct_option_index=0,
                explanation="Original explanation.",
            )
        ],
    )
    assert original_quiz.id is not None
    original_quiz_id = original_quiz.id
    original_plan = repository.create_interview_plan(
        quiz_set=original_quiz,
        outline=[
            {
                "subtopic": "Original subtopic",
                "difficulty": "easy",
                "focus_prompt": "Original interview prompt.",
            }
        ],
        target_questions=1,
        session=session,
    )
    assert original_plan is not None and original_plan.id is not None

    attempt = QuizAttempt(
        quiz_set_id=original_quiz_id,
        user_id=seeded_user.id,
        total_questions=1,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    assert attempt.id is not None
    original_attempt_id = attempt.id

    schedules_before = [
        (row.id, row.interval_label, row.scheduled_date, row.status, row.completed_at)
        for row in session.exec(
            select(ReviewSchedule)
            .where(ReviewSchedule.entry_id == entry_id)
            .order_by(ReviewSchedule.id)  # pyright: ignore[reportArgumentType]
        ).all()
    ]
    assert schedules_before, "POST /api/v1/topics must have scheduled this entry"

    regenerated_draft = MCQDraft(
        question_text="Regenerated question grounded in the saved reference?",
        options=["new-a", "new-b", "new-c", "new-d"],
        correct_option_index=1,
        explanation="Regenerated explanation.",
    )

    class _QuizModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is MCQDrafts
            return self

        def invoke(self, messages):
            assert messages
            return MCQDrafts(drafts=[regenerated_draft])

    regenerated_outline = InterviewOutline(
        items=[
            InterviewOutlineItem(
                subtopic="Regenerated subtopic",
                difficulty="medium",
                focus_prompt="Regenerated interview prompt.",
                source_passage_quote=(
                    "the saved reference now grounds this regenerated interview prompt clearly"
                ),
                ideal_answer_rubric="Explains the regenerated material accurately.",
                bloom_level="understand",
            )
        ]
    )

    class _InterviewModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is InterviewOutline
            return self

        def invoke(self, messages):
            assert messages
            return regenerated_outline

    monkeypatch.setattr(quiz_graph, "_get_draft_model", lambda: _QuizModel())
    monkeypatch.setattr(quiz_graph, "_verify_gate_enabled", lambda: False)
    monkeypatch.setattr(interview_planner, "_get_chat_model", lambda: _InterviewModel())

    response = client.post(f"/api/v1/topics/{entry_id}/regenerate")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["generation_errors"] == [], payload
    assert payload["quiz_set_id"] == original_quiz_id
    assert isinstance(payload["interview_plan_id"], int)

    quiz_sets = session.exec(select(QuizSet).where(QuizSet.entry_id == entry_id)).all()
    assert [row.id for row in quiz_sets] == [original_quiz_id]
    assert session.get(QuizAttempt, original_attempt_id).quiz_set_id == original_quiz_id

    questions = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == original_quiz_id)
    ).all()
    assert [row.question_text for row in questions] == [regenerated_draft.question_text]
    assert questions[0].options == regenerated_draft.options

    refreshed_plan = session.exec(
        select(InterviewPlan).where(InterviewPlan.quiz_set_id == original_quiz_id)
    ).one()
    assert refreshed_plan.id == payload["interview_plan_id"]
    assert refreshed_plan.outline[0]["focus_prompt"] == "Regenerated interview prompt."

    schedules_after = [
        (row.id, row.interval_label, row.scheduled_date, row.status, row.completed_at)
        for row in session.exec(
            select(ReviewSchedule)
            .where(ReviewSchedule.entry_id == entry_id)
            .order_by(ReviewSchedule.id)  # pyright: ignore[reportArgumentType]
        ).all()
    ]
    assert schedules_after == schedules_before
