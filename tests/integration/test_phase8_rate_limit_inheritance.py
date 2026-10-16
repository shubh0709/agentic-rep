"""SRA-805 — Phase 8 interview routes share the existing per-user rate-limit bucket."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from sra.agents.schemas import MCQDraft
from sra.db.models import StudyEntry, StudyEntryCreate

pytestmark = pytest.mark.phase8
SECRET = "rate-limit-test-signing-secret-with-entropy"


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int) -> int:
    from sra.db.repository import create_quiz_set

    entry = _seed_entry(session, user_id)
    drafts = [
        MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")
    ]
    qs = create_quiz_set(
        entry=entry,
        user_id=user_id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=2,
        drafts=drafts,
    )
    return qs.id if qs.id is not None else 0


def _patch_interview_planner(monkeypatch, n: int = 1):
    from sra.agents import interview_planner as _ip

    class _Plan:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            from sra.agents.interview_planner import (
                InterviewOutline,
                InterviewOutlineItem,
            )

            return InterviewOutline(
                items=[
                    InterviewOutlineItem(
                        subtopic=f"s{i}",
                        difficulty="medium",
                        focus_prompt=f"f{i}",
                    )
                    for i in range(n)
                ]
            )

    monkeypatch.setattr(_ip, "_get_chat_model", lambda: _Plan())


@pytest.fixture
def limited_app(monkeypatch, session):
    for key, value in {
        "OPENAI_API_KEY": "test",
        "AUTH_SIGNING_SECRET": SECRET,
        "GOOGLE_CLIENT_ID": "test-client",
        "GOOGLE_CLIENT_SECRET": "test-secret",
        "RATE_LIMIT_AUTHENTICATED_CAPACITY": "2",
        "RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND": "0.5",
        "RATE_LIMIT_ANONYMOUS_CAPACITY": "2",
        "RATE_LIMIT_ANONYMOUS_REFILL_PER_SECOND": "0.5",
    }.items():
        monkeypatch.setenv(key, value)

    import sra.config as _config

    _config.get_settings.cache_clear()

    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    clock = [1000.0]

    from sra.api.app import create_app
    from sra.db.session import get_session

    app = create_app(rate_limit_clock=lambda: clock[0])
    app.dependency_overrides[get_session] = lambda: session
    return app, clock


def _headers(user):
    from sra.auth.google_oauth import issue_app_credential

    token = issue_app_credential(
        user.id, signing_secret=SECRET, ttl_seconds=300, now=datetime.now(UTC)
    )
    return {"Authorization": f"Bearer {token}"}


def test_interview_routes_share_per_user_bucket_with_existing_routes(
    monkeypatch, limited_app, session, seeded_user
):
    """A burst mixing Phase 8 interview routes + existing quiz routes hits 429 from the same bucket."""

    app, _clock = limited_app
    quiz_set_id = _make_quiz_set(session, seeded_user.id)
    _patch_interview_planner(monkeypatch)

    with TestClient(app) as auth_client:
        # capacity 2 means three rapid requests produce one 429.
        responses = [
            auth_client.get("/api/v1/quiz/today", headers=_headers(seeded_user)),
            auth_client.post(
                f"/api/v1/quiz/{quiz_set_id}/attempts",
                json={"mode": "interview"},
                headers=_headers(seeded_user),
            ),
            auth_client.post(
                f"/api/v1/quiz/attempts/{99999}/interview-turn",
                json={"action": "answer", "answer_text": "x"},
                headers=_headers(seeded_user),
            ),
        ]
        codes = [r.status_code for r in responses]
        # First 200 (or 201 for the start), third is 429 because we exhausted the bucket
        assert 429 in codes or all(c < 400 for c in codes[:2]) and codes[-1] == 429, codes


def test_interview_routes_include_limit_authenticated_dependency(client):
    """The new POST interview-turn and GET interview routes both carry the limit_authenticated dep."""

    from fastapi.routing import APIRoute, iter_route_contexts

    from sra.api.rate_limit import limit_authenticated
    from sra.auth.dependencies import require_user

    expected_paths = {
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/interview-turn"),
        ("GET", "/api/v1/quiz/attempts/{attempt_id}/interview"),
    }
    seen: set[tuple[str, str]] = set()

    def dependencies(dependant):
        calls = {dependant.call}
        for child in dependant.dependencies:
            calls.update(dependencies(child))
        return calls

    for route in iter_route_contexts(client.app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        assert route.path is not None
        if route.path is None:
            continue
        assert route.methods is not None
        for method in route.methods:
            key = (method, route.path)
            if key in expected_paths:
                calls = dependencies(route.dependant)
                assert require_user in calls
                assert limit_authenticated in calls
                seen.add(key)

    assert seen == expected_paths
