"""HTTP wrapper contracts, using a fake transport without any live server."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

import json
import math
from types import SimpleNamespace

import httpx
import pytest

from sra.client import api_client
from sra.client.api_client import ApiClientError, ingest_transcript

pytestmark = pytest.mark.phase2

ENTRY = {
    "id": 17,
    "topic": "Mitosis",
    "subtopic": None,
    "tags": ["biology", "cells"],
    "summary": "Mitosis separates duplicated chromosomes.",
    "created_at": "2026-10-13T12:00:00Z",
}


@pytest.fixture
def http_boundary(monkeypatch):
    """Keep the wrapper real; swap only the HTTP transport and configuration."""
    monkeypatch.setattr(api_client, "get_client_settings", lambda: SimpleNamespace(api_base_url="http://testserver"))
    clients = []
    requests = []

    def configure(handler):
        def observed_handler(request):
            requests.append(request)
            return handler(request)

        def create_client():
            client = httpx.Client(transport=httpx.MockTransport(observed_handler))
            clients.append(client)
            return client

        monkeypatch.setattr(api_client, "_create_http_client", create_client)
        return clients, requests

    yield configure
    for client in clients:
        client.close()


def test_http_factory_uses_a_finite_model_call_budget_and_no_redirects():
    client = api_client._create_http_client()
    try:
        for timeout in client.timeout.as_dict().values():
            assert timeout is not None and math.isfinite(timeout) and 0 < timeout <= 90
        assert client.timeout.read == 90
        assert client.follow_redirects is False
    finally:
        client.close()


@pytest.mark.parametrize("base", ["http://testserver", "http://testserver/", "https://testserver/base/"])
def test_one_post_contains_only_transcript_and_returns_entries(monkeypatch, http_boundary, base):
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-secret")
    monkeypatch.setenv("LANGSMITH_API_KEY", "trace-only-secret")
    monkeypatch.setattr(api_client, "get_client_settings", lambda: SimpleNamespace(api_base_url=base))
    returned = [{**ENTRY, "future_field": "preserved"}]
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=returned))
    transcript = "  Today I studied mitosis.\nCells divide!  "

    assert ingest_transcript(transcript) == returned

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{base.rstrip('/')}/api/v1/entries/ingest"
    assert json.loads(request.content) == {"transcript": transcript}
    assert request.headers["content-type"].startswith("application/json")
    assert "authorization" not in request.headers
    assert "x-api-key" not in request.headers
    assert "server-only-secret" not in str(request.headers)
    assert "trace-only-secret" not in str(request.headers)
    assert len(clients) == 1 and clients[0].is_closed


def test_valid_empty_entry_list_is_returned(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=[]))
    assert ingest_transcript("No study topics") == []
    assert len(requests) == 1 and clients[0].is_closed


def test_multiple_entries_preserve_order_and_nullable_or_populated_subtopics(http_boundary):
    entries = [
        {**ENTRY, "id": 23, "subtopic": "Prophase", "tags": [], "created_at": "2026-10-13T17:30:00+05:30"},
        ENTRY,
    ]
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=entries))

    assert ingest_transcript("Mitosis and its phases") == entries
    assert len(requests) == 1 and clients[0].is_closed


def test_connection_failure_is_typed_chained_and_does_not_retry(http_boundary):
    cause = httpx.ConnectError("Connection refused")

    def fail(request):
        raise cause

    clients, requests = http_boundary(fail)
    with pytest.raises(ApiClientError, match="(?i)connect|reach") as caught:
        ingest_transcript("Mitosis")
    assert caught.value.__cause__ is cause
    assert caught.value.status_code is None
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError])
def test_interrupted_request_reports_uncertain_save_and_does_not_retry(http_boundary, error_type):
    cause = error_type("Connection closed during submission")

    def fail(request):
        raise cause

    clients, requests = http_boundary(fail)
    with pytest.raises(ApiClientError, match="(?i)uncertain") as caught:
        ingest_transcript("Mitosis")

    assert caught.value.__cause__ is cause
    assert caught.value.status_code is None
    assert "search" in str(caught.value).lower()
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.parametrize("timeout_type", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout])
def test_timeouts_report_uncertain_save_and_never_retry(http_boundary, timeout_type):
    cause = timeout_type("Timed out after submission")

    def fail(request):
        raise cause

    clients, requests = http_boundary(fail)
    with pytest.raises(ApiClientError, match="(?i)timeout|timed out") as caught:
        ingest_transcript("Mitosis")
    message = str(caught.value).lower()
    assert any(word in message for word in ("may", "might", "unknown", "uncertain", "already"))
    assert any(word in message for word in ("saved", "stored", "committed", "outcome"))
    assert caught.value.__cause__ is cause
    assert caught.value.status_code is None
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.parametrize("status,detail", [
    (200, "Unexpected success status"), (401, "Not authenticated"),
    (422, [{"loc": ["body", "transcript"], "msg": "Field required", "type": "missing"}]),
    (429, {"message": "Try later"}), (500, "Model unavailable"), (503, "Unavailable"),
])
def test_non_201_response_preserves_status_and_fastapi_detail(http_boundary, status, detail):
    clients, requests = http_boundary(lambda request: httpx.Response(status, json={"detail": detail}))
    with pytest.raises(ApiClientError) as caught:
        ingest_transcript("Mitosis")
    assert caught.value.status_code == status
    assert caught.value.detail == detail
    assert str(status) in str(caught.value)
    assert str(caught.value).strip()
    assert len(requests) == 1 and clients[0].is_closed


def test_non_json_http_failure_preserves_status_with_useful_message(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(502, text="Upstream gateway unavailable"))
    with pytest.raises(ApiClientError) as caught:
        ingest_transcript("Mitosis")
    assert caught.value.status_code == 502
    assert "502" in str(caught.value)
    assert any(word in str(caught.value).lower() for word in ("gateway", "upstream", "unavailable"))
    assert len(requests) == 1 and clients[0].is_closed


def test_a_later_explicit_submission_succeeds_after_a_failure(http_boundary):
    responses = iter([httpx.Response(503, json={"detail": "Unavailable"}), httpx.Response(201, json=[ENTRY])])
    clients, requests = http_boundary(lambda request: next(responses))

    with pytest.raises(ApiClientError):
        ingest_transcript("First attempt")
    assert len(requests) == 1 and clients[0].is_closed

    assert ingest_transcript("New explicit attempt") == [ENTRY]
    assert len(requests) == 2
    assert len(clients) == 2 and all(client.is_closed for client in clients)
    assert json.loads(requests[1].content) == {"transcript": "New explicit attempt"}


def test_redirect_is_an_error_without_a_second_submission(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(307, headers={"location": "http://other/ingest"}))
    with pytest.raises(ApiClientError) as caught:
        ingest_transcript("Mitosis")
    assert caught.value.status_code == 307
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.phase3
def test_authenticated_submission_sends_bearer_once_without_putting_it_in_url_or_body(http_boundary):
    credential = "server-issued-application-credential"
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=[ENTRY]))

    assert ingest_transcript("Mitosis", credential=credential) == [ENTRY]

    assert len(requests) == 1
    request = requests[0]
    assert request.headers["authorization"] == f"Bearer {credential}"
    assert credential not in str(request.url)
    assert credential not in request.content.decode()
    assert json.loads(request.content) == {"transcript": "Mitosis"}
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.phase3
def test_authentication_failure_does_not_retry_ingestion(http_boundary):
    credential = "expired-application-credential"
    clients, requests = http_boundary(lambda request: httpx.Response(401, json={"detail": "Sign in again"}))

    with pytest.raises(ApiClientError, match="(?i)sign in|401") as caught:
        ingest_transcript("Mitosis", credential=credential)

    assert caught.value.status_code == 401
    assert len(requests) == 1
    assert len(clients) == 1 and clients[0].is_closed
    assert credential not in str(caught.value)


def test_invalid_json_is_a_typed_error_and_closes_client(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(201, text="not JSON"))
    with pytest.raises(ApiClientError, match="(?i)json|response") as caught:
        ingest_transcript("Mitosis")
    assert caught.value.status_code == 201
    assert caught.value.__cause__ is not None
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.parametrize("body", [None, "entries", 7, {}, {"entries": [ENTRY]}, ["entry"], [None], [{}]])
def test_malformed_entry_list_is_a_typed_error(http_boundary, body):
    clients, requests = http_boundary(lambda request: httpx.Response(201, content=json.dumps(body)))
    with pytest.raises(ApiClientError, match="(?i)entry|entries|response") as caught:
        ingest_transcript("Mitosis")
    assert caught.value.status_code == 201
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.parametrize("field", list(ENTRY))
def test_missing_entry_fields_are_rejected(http_boundary, field):
    incomplete = {name: value for name, value in ENTRY.items() if name != field}
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=[incomplete]))
    with pytest.raises(ApiClientError):
        ingest_transcript("Mitosis")
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.parametrize("field,value", [
    ("id", "17"), ("id", True), ("topic", None), ("subtopic", 2),
    ("tags", "biology"), ("tags", [42]), ("summary", None),
    ("created_at", "yesterday"), ("created_at", None),
    ("created_at", 1791892800), ("created_at", "2026-10-13"),
    ("created_at", "1791892800"),
    ("created_at", "2026-02-30T12:00:00Z"),
])
def test_invalid_entry_fields_are_rejected_even_after_a_valid_entry(http_boundary, field, value):
    invalid = {**ENTRY, field: value}
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=[ENTRY, invalid]))
    with pytest.raises(ApiClientError):
        ingest_transcript("Mitosis")
    assert len(requests) == 1 and clients[0].is_closed
@pytest.mark.phase3
@pytest.mark.parametrize("failure", ["connect", "read", "body", "validation"])
def test_authenticated_failures_never_echo_credentials(monkeypatch, failure):
    import traceback

    from sra.client import api_client

    secret = "private-bearer-value"

    def send(request):
        if failure == "connect":
            raise httpx.ConnectError(secret, request=request)
        if failure == "read":
            raise httpx.ReadError(secret, request=request)
        if failure == "body":
            return httpx.Response(502, json={"detail": {"authorization": secret}})
        return httpx.Response(201, json=[{"id": secret}])

    monkeypatch.setattr(api_client, "_create_http_client", lambda: httpx.Client(transport=httpx.MockTransport(send)))
    with pytest.raises(api_client.ApiClientError) as caught:
        api_client.ingest_transcript("A note", credential=secret)
    assert secret not in "".join(traceback.format_exception(caught.value))
    assert secret not in repr(caught.value.detail)


# ---------------------------------------------------------------------------
# SRA-506 client API extensions: digest + voice quiz
# ---------------------------------------------------------------------------


DIGEST_ROW = {
    "id": 1,
    "entry": {**ENTRY},
    "interval_label": "1d",
    "scheduled_date": "2026-10-14",
    "status": "pending",
}


@pytest.mark.phase5
def test_get_digest_ready_returns_true_and_calls_ready_route(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(200, json={"ready": True}))
    result = api_client.get_digest_ready(credential="token-1")
    assert result is True
    assert len(requests) == 1
    assert str(requests[0].url) == "http://testserver/api/v1/digest/ready"
    assert requests[0].method == "GET"
    assert requests[0].headers["authorization"] == "Bearer token-1"
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.phase5
def test_get_digest_ready_returns_false_when_not_due(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(200, json={"ready": False}))
    result = api_client.get_digest_ready(credential="token-1")
    assert result is False
    assert len(requests) == 1


@pytest.mark.phase5
def test_get_digest_ready_auth_required_on_401(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(401, json={"detail": "Sign in"}))
    with pytest.raises(api_client.ApiClientError, match="(?i)auth_required|sign in"):
        api_client.get_digest_ready(credential="token-1")
    assert len(requests) == 1


@pytest.mark.phase5
def test_get_digest_ready_rate_limited_includes_retry_after(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(429, json={"detail": "Rate limit exceeded"}, headers={"retry-after": "7"})
    )
    with pytest.raises(api_client.ApiClientError) as caught:
        api_client.get_digest_ready(credential="token-1")
    assert caught.value.status_code == 429
    assert getattr(caught.value, "retry_after", None) == 7 or "7" in str(caught.value)


@pytest.mark.phase5
def test_get_digest_text_formats_a_readable_sentence_from_review_rows(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(200, json=[DIGEST_ROW, {**DIGEST_ROW, "id": 2, "entry": {**ENTRY, "id": 18, "topic": "Meiosis"}}])
    )
    sentence = api_client.get_digest_text(credential="token-1")
    assert "2 reviews" in sentence
    assert "Mitosis" in sentence
    assert "Meiosis" in sentence
    assert len(requests) == 1
    assert str(requests[0].url) == "http://testserver/api/v1/digest"


@pytest.mark.phase5
def test_get_digest_text_with_empty_list_returns_specific_sentence(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(200, json=[]))
    sentence = api_client.get_digest_text(credential="token-1")
    assert "no reviews" in sentence.lower()


@pytest.mark.phase5
def test_start_voice_quiz_attempt_issues_one_post_and_returns_dict(http_boundary):
    payload = {"attempt_id": 42, "question_text": "q0", "options": ["a", "b"]}
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=payload))
    result = api_client.start_voice_quiz_attempt(quiz_set_id=7, credential="token-1")
    assert result == payload
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://testserver/api/v1/quiz/7/attempts"
    assert json.loads(request.content) == {"mode": "voice"}
    assert request.headers["authorization"] == "Bearer token-1"


@pytest.mark.phase5
def test_start_voice_quiz_attempt_maps_phase4_voice_rejection_to_typed_error(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(
            400, json={"detail": "Voice mode is not yet supported. See Phase 5."}
        )
    )
    with pytest.raises(api_client.ApiClientError, match="(?i)phase5|phase 5|not yet supported"):
        api_client.start_voice_quiz_attempt(quiz_set_id=7, credential="token-1")
    assert len(requests) == 1


@pytest.mark.phase5
def test_send_voice_turn_forwards_answer_text_and_returns_dict(http_boundary):
    payload = {
        "is_correct": True,
        "feedback_text": "Correct.",
        "next_question_text": "q1",
        "finished": False,
        "score": 1,
    }
    clients, requests = http_boundary(lambda request: httpx.Response(200, json=payload))
    result = api_client.send_voice_turn(
        attempt_id=42, answer_text="the second one", credential="token-1"
    )
    assert result == payload
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://testserver/api/v1/quiz/attempts/42/voice-turn"
    assert json.loads(request.content) == {"answer_text": "the second one"}
    assert request.headers["authorization"] == "Bearer token-1"


@pytest.mark.phase5
def test_send_voice_turn_maps_409_completed_to_typed_error(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(409, json={"detail": "Attempt already completed"})
    )
    with pytest.raises(api_client.ApiClientError, match="(?i)attempt_completed|already completed"):
        api_client.send_voice_turn(attempt_id=42, answer_text="ignored", credential="token-1")
    assert len(requests) == 1


@pytest.mark.phase5
def test_client_methods_close_clients_under_repeated_calls(http_boundary):
    for _ in range(3):
        clients, requests = http_boundary(lambda request: httpx.Response(200, json={"ready": True}))
        api_client.get_digest_ready(credential="token-1")
        assert clients[0].is_closed


@pytest.mark.phase5
def test_client_methods_pass_credentials_only_in_bearer_header_not_in_url(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(201, json={"attempt_id": 1, "question_text": "q", "options": []})
    )
    secret = "server-issued-application-credential"
    api_client.start_voice_quiz_attempt(quiz_set_id=1, credential=secret)
    assert requests[0].headers["authorization"] == f"Bearer {secret}"
    assert secret not in str(requests[0].url)
    assert secret not in requests[0].content.decode()


# ---------------------------------------------------------------------------
# SRA-808 — Interview client API extensions: ``start_interview_attempt``,
# ``send_interview_turn``, ``get_interview``.
# ---------------------------------------------------------------------------


phase8 = pytest.mark.phase8  # noqa: E402  -- marker alias below the Phase 5 marker


@phase8
def test_start_interview_attempt_posts_mode_interview_and_returns_dict(http_boundary):
    payload = {
        "attempt_id": 42,
        "question_text": "first q",
        "transcript_so_far": [],
    }
    clients, requests = http_boundary(
        lambda request: httpx.Response(201, json=payload)
    )

    result = api_client.start_interview_attempt(quiz_set_id=7, credential="token-1")

    assert result == payload
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://testserver/api/v1/quiz/7/attempts"
    assert json.loads(request.content) == {"mode": "interview"}
    assert request.headers["authorization"] == "Bearer token-1"
    assert len(clients) == 1 and clients[0].is_closed


@phase8
def test_send_interview_turn_posts_action_and_answer_text(http_boundary):
    payload = {
        "question_text": "next",
        "score": 80,
        "rationale": "ok",
        "feedback": "good",
        "finished": False,
        "summary": None,
        "transcript_so_far": [],
    }
    clients, requests = http_boundary(
        lambda request: httpx.Response(200, json=payload)
    )

    result = api_client.send_interview_turn(
        attempt_id=42, action="answer", answer_text="the answer", credential="token-1"
    )

    assert result == payload
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://testserver/api/v1/quiz/attempts/42/interview-turn"
    assert json.loads(request.content) == {"action": "answer", "answer_text": "the answer"}
    assert request.headers["authorization"] == "Bearer token-1"


@phase8
def test_send_interview_turn_next_action_omits_answer_text(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(200, json={"question_text": "q", "score": None, "finished": False})
    )

    api_client.send_interview_turn(
        attempt_id=42, action="next", answer_text=None, credential="token-1"
    )

    body = json.loads(requests[0].content)
    assert body["action"] == "next"
    assert body.get("answer_text") is None


@phase8
def test_get_interview_gets_attempt_endpoint(http_boundary):
    payload = {
        "plan": {"id": 1, "quiz_set_id": 1, "outline": [], "target_questions": 0, "created_at": "2026-10-14T12:00:00Z"},
        "turns": [],
        "summary": None,
        "attempt": {
            "id": 42,
            "quiz_set_id": 1,
            "mode": "interview",
            "started_at": "2026-10-14T12:00:00Z",
            "completed_at": None,
            "score": None,
            "total_questions": 1,
            "cursor_on_plan": 0,
        },
    }
    clients, requests = http_boundary(lambda request: httpx.Response(200, json=payload))

    result = api_client.get_interview(attempt_id=42, credential="token-1")

    assert result == payload
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == "http://testserver/api/v1/quiz/attempts/42/interview"
    assert request.headers["authorization"] == "Bearer token-1"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 500])
@phase8
def test_interview_client_methods_raise_typed_error_on_4xx_or_5xx(
    http_boundary, status
):
    clients, requests = http_boundary(
        lambda request: httpx.Response(status, json={"detail": "boom"})
    )

    with pytest.raises(api_client.ApiClientError) as caught:
        api_client.start_interview_attempt(quiz_set_id=1, credential="token-1")

    assert caught.value.status_code == status
    assert len(requests) == 1


@phase8
def test_interview_client_methods_pass_credentials_only_in_bearer_header(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(201, json={"attempt_id": 1, "question_text": "q", "transcript_so_far": []})
    )
    secret = "server-issued-application-credential"
    api_client.start_interview_attempt(quiz_set_id=1, credential=secret)
    assert requests[0].headers["authorization"] == f"Bearer {secret}"
    assert secret not in str(requests[0].url)
    assert secret not in requests[0].content.decode()


@phase8
def test_interview_client_module_exposes_the_required_helpers():
    """TDD: the desktop wrapper must export the three Phase 8 methods."""
    for name in ("start_interview_attempt", "send_interview_turn", "get_interview"):
        assert hasattr(api_client, name), name

