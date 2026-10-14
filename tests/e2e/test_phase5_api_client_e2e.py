"""SRA-506 e2e — Four new api_client methods against a httpx MockTransport."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

import json
from types import SimpleNamespace

import httpx
import pytest

from sra.client import api_client

pytestmark = pytest.mark.phase5


ENTRY = {
    "id": 17,
    "topic": "Mitosis",
    "subtopic": None,
    "tags": ["biology", "cells"],
    "summary": "Mitosis separates duplicated chromosomes.",
    "created_at": "2026-10-13T12:00:00Z",
}

DIGEST_ROW = {
    "id": 1,
    "entry": ENTRY,
    "interval_label": "1d",
    "scheduled_date": "2026-10-14",
    "status": "pending",
}


@pytest.fixture
def http_boundary(monkeypatch):
    monkeypatch.setattr(api_client, "get_client_settings", lambda: SimpleNamespace(api_base_url="http://testserver"))
    clients: list[httpx.Client] = []
    requests: list[httpx.Request] = []

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


def test_phase5_api_client_get_digest_ready_one_request_returns_bool(http_boundary):
    clients, requests = http_boundary(lambda request: httpx.Response(200, json={"ready": True}))
    assert api_client.get_digest_ready(credential="t-1") is True
    assert len(requests) == 1
    assert str(requests[0].url) == "http://testserver/api/v1/digest/ready"
    assert requests[0].method == "GET"
    assert requests[0].headers["authorization"] == "Bearer t-1"
    assert clients[0].is_closed


def test_phase5_api_client_get_digest_text_returns_single_sentence(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(200, json=[DIGEST_ROW, {**DIGEST_ROW, "id": 2, "entry": {**ENTRY, "id": 18, "topic": "Meiosis"}}])
    )
    sentence = api_client.get_digest_text(credential="t-1")
    assert "2" in sentence
    assert "Mitosis" in sentence
    assert "Meiosis" in sentence
    assert str(requests[0].url) == "http://testserver/api/v1/digest"


def test_phase5_api_client_start_voice_quiz_attempt_returns_inline_first_question(http_boundary):
    payload = {"attempt_id": 1, "question_text": "q0", "options": ["a", "b"]}
    clients, requests = http_boundary(lambda request: httpx.Response(201, json=payload))
    result = api_client.start_voice_quiz_attempt(quiz_set_id=7, credential="t-1")
    assert result == payload
    assert json.loads(requests[0].content) == {"mode": "voice"}
    assert str(requests[0].url) == "http://testserver/api/v1/quiz/7/attempts"


def test_phase5_api_client_start_voice_quiz_attempt_400_phase4_maps_to_typed_error(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(400, json={"detail": "Voice mode is not yet supported. See Phase 5."})
    )
    with pytest.raises(api_client.ApiClientError, match="(?i)phase5|phase 5|not yet supported"):
        api_client.start_voice_quiz_attempt(quiz_set_id=7, credential="t-1")


def test_phase5_api_client_send_voice_turn_returns_response(http_boundary):
    payload = {
        "is_correct": True,
        "feedback_text": "Correct.",
        "next_question_text": "q1",
        "finished": False,
        "score": 1,
    }
    clients, requests = http_boundary(lambda request: httpx.Response(200, json=payload))
    result = api_client.send_voice_turn(attempt_id=99, answer_text="the second one", credential="t-1")
    assert result == payload
    assert json.loads(requests[0].content) == {"answer_text": "the second one"}
    assert str(requests[0].url) == "http://testserver/api/v1/quiz/attempts/99/voice-turn"


def test_phase5_api_client_send_voice_turn_409_maps_to_typed_error(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(409, json={"detail": "Attempt already completed"})
    )
    with pytest.raises(api_client.ApiClientError, match="(?i)attempt_completed|already completed"):
        api_client.send_voice_turn(attempt_id=99, answer_text="again", credential="t-1")


def test_phase5_api_client_429_includes_retry_after(http_boundary):
    clients, requests = http_boundary(
        lambda request: httpx.Response(
            429, json={"detail": "Rate limit exceeded"}, headers={"retry-after": "12"}
        )
    )
    with pytest.raises(api_client.ApiClientError) as caught:
        api_client.get_digest_ready(credential="t-1")
    assert caught.value.status_code == 429
    detail = getattr(caught.value, "retry_after", None)
    assert detail == 12 or "12" in str(caught.value)
