"""Phase 3 authenticated client-to-storage contracts.

These tests replace only network location and model inference.  Application
credentials, FastAPI dependencies/routes, ingestion graph, repository,
scheduler, search, and SQLite persistence remain real.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import select

from sra.agents.schemas import StudyEntryDraft, StudyEntryDrafts
from sra.auth.google_oauth import issue_app_credential
from sra.client import api_client
from sra.client.api_client import ApiClientError, ingest_transcript
from sra.config import get_settings
from sra.db.models import ReviewSchedule, StudyEntry, User, UserRole

pytestmark = pytest.mark.phase3
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
SIGNING_SECRET = "phase-three-e2e-signing-secret"


@pytest.fixture
def authenticated_capture(client, monkeypatch, session):
    monkeypatch.setenv("AUTH_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("API_BASE_URL", "http://phase3.test")
    get_settings.cache_clear()
    client.headers.pop("Authorization", None)

    users = {
        "a": User(email="a@example.com", display_name="User A", google_sub="subject-a"),
        "b": User(email="b@example.com", display_name="User B", google_sub="subject-b"),
    }
    session.add_all(users.values())
    session.commit()
    for user in users.values():
        session.refresh(user)

    requests = []
    clients = []

    class FakeExtractionModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is StudyEntryDrafts
            return self

        def invoke(self, messages):
            transcript = json.loads(requests[-1].content)["transcript"]
            marker = "Alpha" if "alpha" in transcript.lower() else "Beta"
            return StudyEntryDrafts(
                drafts=[
                    StudyEntryDraft(
                        topic=f"{marker} authenticated topic",
                        tags=["phase3"],
                        summary=f"Summary for {marker}",
                    )
                ]
            )

    def send(request):
        requests.append(request)
        response = client.request(
            request.method,
            str(request.url),
            content=request.content,
            headers=request.headers,
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    def create_http_client():
        connection = httpx.Client(transport=httpx.MockTransport(send), timeout=90, follow_redirects=False)
        clients.append(connection)
        return connection

    monkeypatch.setattr(api_client, "_create_http_client", create_http_client)
    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", FakeExtractionModel)
    with client:
        yield users, requests, clients


def credential_for(user):
    assert user.id is not None
    return issue_app_credential(
        user.id,
        signing_secret=SIGNING_SECRET,
        ttl_seconds=300,
        now=datetime.now(UTC),
    )


def test_authenticated_capture_persists_exact_ids_under_resolved_user(authenticated_capture, session):
    users, requests, clients = authenticated_capture
    credential = credential_for(users["a"])
    transcript = "Alpha user studied thylakoid proton gradients."

    returned = ingest_transcript(transcript, credential=credential)

    assert len(requests) == 1
    assert requests[0].headers["authorization"] == f"Bearer {credential}"
    assert credential not in str(requests[0].url)
    assert json.loads(requests[0].content) == {"transcript": transcript}
    assert all(connection.is_closed for connection in clients)

    assert len(returned) == 1
    entry_id = returned[0]["id"]
    stored = session.get(StudyEntry, entry_id)
    assert stored is not None
    assert stored.user_id == users["a"].id
    assert stored.raw_transcript == transcript
    reviews = session.exec(select(ReviewSchedule).where(ReviewSchedule.entry_id == entry_id)).all()
    assert len(reviews) == 6


def test_two_credentials_cannot_search_or_digest_each_others_entries(authenticated_capture):
    users, _, _ = authenticated_capture
    credentials = {name: credential_for(user) for name, user in users.items()}

    alpha = ingest_transcript("Alpha user studied a unique chloroplast marker.", credential=credentials["a"])[0]
    beta = ingest_transcript("Beta user studied a unique chromosome marker.", credential=credentials["b"])[0]

    # Broad queries are important: isolation must not depend on choosing a
    # search term that happens not to match the other user's content.
    alpha_search = authenticated_capture_request("GET", "/api/v1/search?q=", credentials["a"])
    beta_search = authenticated_capture_request("GET", "/api/v1/search?q=", credentials["b"])
    assert {row["id"] for row in alpha_search.json()} == {alpha["id"]}
    assert {row["id"] for row in beta_search.json()} == {beta["id"]}
    assert beta["id"] not in {row["id"] for row in alpha_search.json()}
    assert alpha["id"] not in {row["id"] for row in beta_search.json()}


def authenticated_capture_request(method, path, credential):
    """Use the same in-process transport installed by the fixture."""
    with api_client._create_http_client() as connection:
        response = connection.request(method, f"http://phase3.test{path}", headers={"Authorization": f"Bearer {credential}"})
    assert response.status_code == 200
    return response


def test_rejected_credential_causes_no_write_and_no_automatic_retry(authenticated_capture, session):
    _, requests, clients = authenticated_capture

    with pytest.raises(ApiClientError) as caught:
        ingest_transcript("Alpha should not be stored.", credential="invalid-credential")

    assert caught.value.status_code == 401
    assert len(requests) == 1
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    assert all(connection.is_closed for connection in clients)
    assert "invalid-credential" not in str(caught.value)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/entries/ingest", {"transcript": "Must not reach the model"}),
        ("GET", "/api/v1/search?q=", None),
        ("GET", "/api/v1/digest", None),
        ("POST", "/api/v1/chat", {"question": "What did I study?"}),
    ],
)
def test_every_study_route_rejects_missing_credentials_before_work(
    authenticated_capture,
    monkeypatch,
    method,
    path,
    body,
):
    _, _, _ = authenticated_capture
    model_calls = []

    class CountingIngestionModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            model_calls.append("ingestion")
            return StudyEntryDrafts(
                drafts=[StudyEntryDraft(topic="Should be unreachable", summary="Authentication must run first")]
            )

    class CountingQueryModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            model_calls.append("query")
            return {"role": "ai", "tool_calls": [], "content": "Should be unreachable"}

    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", CountingIngestionModel)
    monkeypatch.setattr("sra.agents.query_graph._get_chat_model", CountingQueryModel)

    with api_client._create_http_client() as connection:
        response = connection.request(method, f"http://phase3.test{path}", json=body)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert model_calls == []


def test_admin_usage_is_forbidden_to_user_and_contains_aggregates_only_for_admin(authenticated_capture, session):
    users, _, _ = authenticated_capture
    users["b"].role = UserRole.ADMIN
    session.add(users["b"])
    session.commit()

    user_response = authenticated_capture_request_raw("GET", "/api/v1/admin/usage", credential_for(users["a"]))
    assert user_response.status_code == 403

    admin_response = authenticated_capture_request_raw("GET", "/api/v1/admin/usage", credential_for(users["b"]))
    assert admin_response.status_code == 200
    body = admin_response.json()
    assert set(body) >= {"user_count", "entry_count"}
    serialized = json.dumps(body).lower()
    for forbidden in ("raw_transcript", "summary", "topic", "credential", "subject-a"):
        assert forbidden not in serialized


def authenticated_capture_request_raw(method, path, credential):
    with api_client._create_http_client() as connection:
        return connection.request(
            method,
            f"http://phase3.test{path}",
            headers={"Authorization": f"Bearer {credential}"},
        )


def test_restarted_secure_store_to_voice_pipeline_two_user_chat_and_digest(
    authenticated_capture, whisper_boundary, monkeypatch, session,
):
    from langchain_core.messages import AIMessage, ToolMessage

    from sra.capture.pipeline import process_recording
    from sra.client import auth_client

    users, requests, clients = authenticated_capture
    values = {}
    backend = SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, value: values.__setitem__((service, account), value),
        delete_password=lambda service, account: values.pop((service, account)),
    )
    monkeypatch.setattr(auth_client.sys, "platform", "darwin")
    monkeypatch.setattr(auth_client, "_create_http_client", api_client._create_http_client)
    origin = "http://phase3.test"
    store = auth_client.MacOSKeychainCredentialStore(backend=backend)
    audio = Path(__file__).parents[1] / "fixtures" / "study_note.wav"
    ids = {}
    credentials = {}
    for name, user in users.items():
        credential = credential_for(user)
        store.save(server_origin=origin, account=user.google_sub, credential=credential)
        # Recreate the adapter to exercise the persisted active-account selector.
        restarted = auth_client.MacOSKeychainCredentialStore(backend=backend)
        account = auth_client.load_default_authenticated_account(api_base_url=origin, store=restarted)
        assert account.subject == user.google_sub
        credentials[name] = account.credential
        whisper_boundary.segments = ["Alpha unique marker" if name == "a" else "Beta unique marker"]
        result = process_recording(audio, credential=account.credential)
        ids[name] = result[0]["id"]
        stored = session.get(StudyEntry, ids[name])
        assert stored is not None and stored.user_id == user.id
        reviews = session.exec(select(ReviewSchedule).where(ReviewSchedule.entry_id == stored.id)).all()
        assert len(reviews) == 6
        assert min(row.scheduled_date for row in reviews) == stored.created_at.date() + timedelta(days=1)

    class Tomorrow(date):
        @classmethod
        def today(cls):
            return date.today() + timedelta(days=1)

    monkeypatch.setattr("sra.api.routers.digest.date", Tomorrow)
    for name, credential in credentials.items():
        other = "b" if name == "a" else "a"
        digest = authenticated_capture_request("GET", "/api/v1/digest", credential).json()
        assert {row["entry"]["id"] for row in digest} == {ids[name]}

        class QueryModel:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages, foreign_id=ids[other]):
                results = [message.content for message in messages if isinstance(message, ToolMessage)]
                if results:
                    assert "Study entry not found." in results
                    assert not any(f"Entry ID: {foreign_id}\n" in str(result) for result in results)
                    return AIMessage(content="\n".join(str(result) for result in results))
                return AIMessage(content="", tool_calls=[
                    {"name": "search_entries_tool", "args": {"query": ""}, "id": "broad"},
                    {"name": "get_entry_detail_tool", "args": {"entry_id": foreign_id}, "id": "foreign"},
                    {"name": "get_due_reviews_tool", "args": {"as_of": Tomorrow.today().isoformat()}, "id": "due"},
                ])

        monkeypatch.setattr("sra.agents.query_graph._get_chat_model", QueryModel)
        with api_client._create_http_client() as connection:
            response = connection.post(
                f"{origin}/api/v1/chat", json={"question": "Show all records, including the other account"},
                headers={"Authorization": f"Bearer {credential}"},
            )
        assert response.status_code == 200
        assert f"Entry ID: {ids[name]}" in response.json()["answer"]
        assert f"Entry ID: {ids[other]}\n" not in response.json()["answer"]
    assert sum(request.url.path.endswith("/entries/ingest") for request in requests) == 2
    assert all(connection.is_closed for connection in clients)
