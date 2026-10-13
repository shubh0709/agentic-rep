"""HTTP wrapper contracts, using a fake transport without any live server."""

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
