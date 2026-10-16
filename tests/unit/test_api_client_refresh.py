"""SRA-306 desktop API client one-shot 401 refresh retry."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import httpx
import pytest

from sra.client import api_client
from sra.client.api_client import ApiClientError

pytestmark = pytest.mark.phase3


@dataclass
class MemoryCredentialStore:
    values: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    saves: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    def load(self, *, server_origin, account):
        return self.values.get((server_origin, account))

    def save(self, *, server_origin, account, credential):  # pragma: no cover - placeholder
        self.values[(server_origin, account)] = credential
        self.saves.append((server_origin, account, credential))

    def save_pair(self, *, server_origin, account, credential, refresh_credential):
        pair = {"credential": credential, "refresh_credential": refresh_credential}
        self.values[(server_origin, account)] = pair
        self.saves.append((server_origin, account, pair))


@pytest.fixture
def http_boundary(monkeypatch):
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


ENTRY = {"id": 1, "topic": "T", "subtopic": None, "tags": [], "summary": "S", "created_at": "2026-10-14T10:00:00Z"}


def test_401_from_study_route_triggers_one_refresh_and_returns_successful_retry(http_boundary, monkeypatch):
    refresh_response = {
        "credential": "new-access",
        "refresh_credential": "new-refresh",
        "token_type": "Bearer",
        "expires_in": 300,
        "refresh_expires_in": 600,
    }
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url), dict(request.headers)))
        if request.url.path.endswith("/api/v1/auth/refresh"):
            return httpx.Response(200, json=refresh_response)
        if dict(request.headers).get("authorization", "").endswith("new-access"):
            return httpx.Response(201, json=[ENTRY])
        return httpx.Response(401, json={"detail": "expired"})

    clients, requests = http_boundary(handler)
    store = MemoryCredentialStore()

    result = api_client.ingest_transcript(
        "A note",
        credential="old-access",
        refresh_credential="old-refresh",
        credential_store=store,
        server_origin="http://testserver",
    )

    assert result == [ENTRY]
    refresh_calls = [c for c in calls if c[0] == "POST" and c[1].endswith("/api/v1/auth/refresh")]
    ingest_calls = [c for c in calls if c[1].endswith("/api/v1/entries/ingest")]
    assert len(refresh_calls) == 1
    assert len(ingest_calls) == 2
    assert store.saves and store.saves[0][2]["credential"] == "new-access"


def test_second_401_after_successful_refresh_raises_typed_error(http_boundary, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if "/auth/refresh" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "credential": "new-access", "refresh_credential": "new-refresh",
                    "token_type": "Bearer", "expires_in": 300, "refresh_expires_in": 600,
                },
            )
        return httpx.Response(401, json={"detail": "still expired"})

    clients, requests = http_boundary(handler)
    store = MemoryCredentialStore()

    with pytest.raises(ApiClientError, match="(?i)sign in|auth") as caught:
        api_client.ingest_transcript(
            "A note", credential="old", refresh_credential="old-r",
            credential_store=store, server_origin="http://testserver",
        )

    refresh_calls = [r for r in calls if "/auth/refresh" in str(r.url)]
    ingest_calls = [r for r in calls if str(r.url).endswith("/api/v1/entries/ingest")]
    assert len(refresh_calls) == 1
    assert len(ingest_calls) == 2
    assert caught.value.status_code == 401
    assert "old" not in str(caught.value)


def test_401_from_refresh_endpoint_raises_typed_error_without_looping(http_boundary, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if "/auth/refresh" in str(request.url):
            return httpx.Response(401, json={"detail": "refresh gone"})
        return httpx.Response(401, json={"detail": "expired"})

    clients, requests = http_boundary(handler)
    store = MemoryCredentialStore()

    with pytest.raises(ApiClientError, match="(?i)sign in|auth|refresh"):
        api_client.ingest_transcript(
            "A note", credential="old", refresh_credential="old-r",
            credential_store=store, server_origin="http://testserver",
        )

    refresh_calls = [r for r in calls if "/auth/refresh" in str(r.url)]
    ingest_calls = [r for r in calls if str(r.url).endswith("/api/v1/entries/ingest")]
    assert len(refresh_calls) == 1
    assert len(ingest_calls) == 1
