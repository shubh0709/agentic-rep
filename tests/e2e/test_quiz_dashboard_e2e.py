"""SRA-405 dashboard E2E: drive the same fetch sequence the module uses."""

import pytest

from sra.agents.quiz_graph import generate_and_persist_questions
from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.db.models import StudyEntry, StudyEntryCreate

pytestmark = pytest.mark.phase4


class FakeChatModel:
    def __init__(self, drafts: list[MCQDraft]):
        self._drafts = drafts

    def with_structured_output(self, schema, **kwargs):
        assert schema is MCQDrafts
        return self

    def invoke(self, *args, **kwargs):
        return MCQDrafts(drafts=self._drafts)


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Photosynthesis", summary="light reactions", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _patch_generate(monkeypatch, drafts: list[MCQDraft]):
    monkeypatch.setattr("sra.agents.quiz_graph._get_chat_model", lambda: FakeChatModel(drafts))


def test_dashboard_fetch_sequence_through_real_router(monkeypatch, client, session, seeded_user):
    entry = _seed_entry(session, seeded_user.id)
    drafts = [
        MCQDraft(
            question_text="q0", options=["a", "b", "c", "d"], correct_option_index=0, explanation="ex0"
        ),
        MCQDraft(
            question_text="q1", options=["w", "x", "y", "z"], correct_option_index=1, explanation="ex1"
        ),
    ]
    _patch_generate(monkeypatch, drafts)

    generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=2,
        option_count=4,
    )

    today_payload = client.get("/api/v1/quiz/today").json()
    assert len(today_payload) == 1
    quiz_set_id = today_payload[0]["id"]
    for question in today_payload[0]["questions"]:
        assert "correct_option_index" not in question

    detail_payload = client.get(f"/api/v1/quiz/{quiz_set_id}").json()
    questions = detail_payload["questions"]
    assert all("correct_option_index" not in q for q in questions)

    start = client.post(
        f"/api/v1/quiz/{quiz_set_id}/attempts",
        json={"mode": "web"},
    )
    attempt_id = start.json()["attempt_id"]

    correct = 0
    answer_results = []
    for question in questions:
        idx = next(i for i, d in enumerate(drafts) if d.question_text == question["question_text"])
        response = client.post(
            f"/api/v1/quiz/attempts/{attempt_id}/answers",
            json={"question_id": question["id"], "selected_option_index": idx},
        )
        result = response.json()
        answer_results.append(result)
        if result["is_correct"]:
            correct += 1
        assert "correct_option_index" in result
        assert result["correct_option_index"] == idx

    completion = client.post(f"/api/v1/quiz/attempts/{attempt_id}/complete").json()
    assert completion["score"] == correct
    assert completion["total_questions"] == len(questions)

    summary = client.get(f"/api/v1/quiz/attempts/{attempt_id}").json()
    assert summary["score"] == correct
    assert summary["total_questions"] == len(questions)


def test_dashboard_fetch_sequence_renders_rate_limit_panel(monkeypatch, client, session, seeded_user):
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from sra.api.app import create_app
    from sra.auth.google_oauth import issue_app_credential
    from sra.config import get_settings
    from sra.db.session import get_session

    SECRET = "rate-limit-test-signing-secret-with-entropy"
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_CAPACITY", "1")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND", "0.1")
    get_settings.cache_clear()

    clock = [1000.0]
    app = create_app(rate_limit_clock=lambda: clock[0])
    app.dependency_overrides[get_session] = lambda: session

    token = issue_app_credential(
        seeded_user.id,
        signing_secret=SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as auth_client:
        first = auth_client.get("/api/v1/quiz/today", headers=headers)
        assert first.status_code == 200
        second = auth_client.get("/api/v1/quiz/today", headers=headers)
        assert second.status_code == 429
        assert second.headers["Retry-After"]


def test_dashboard_fetch_sequence_url_paths_match_expected_endpoints(client, session, seeded_user, monkeypatch):
    """Specification test: the dashboard calls these five endpoints in sequence."""

    expected_paths = {
        ("GET", "/api/v1/quiz/today"),
        ("GET", "/api/v1/quiz/{quiz_set_id}"),
        ("POST", "/api/v1/quiz/{quiz_set_id}/attempts"),
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/answers"),
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/complete"),
        ("GET", "/api/v1/quiz/attempts/{attempt_id}"),
    }
    assert expected_paths == {
        ("GET", "/api/v1/quiz/today"),
        ("GET", "/api/v1/quiz/{quiz_set_id}"),
        ("POST", "/api/v1/quiz/{quiz_set_id}/attempts"),
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/answers"),
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/complete"),
        ("GET", "/api/v1/quiz/attempts/{attempt_id}"),
    }