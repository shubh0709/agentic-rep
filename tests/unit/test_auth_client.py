"""SRA-303 desktop login and secure credential lifecycle contracts."""

import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from sra.client import auth_client
from sra.client.auth_client import (
    DesktopAuthError,
    LoopbackResult,
    MacOSKeychainCredentialStore,
    load_authenticated_account,
    normalize_server_origin,
    receive_loopback_completion,
    sign_in,
    sign_out,
)

pytestmark = pytest.mark.phase3


@dataclass
class MemoryCredentialStore:
    values: dict[tuple[str, str], str] = field(default_factory=dict)
    saves: list[tuple[str, str, str]] = field(default_factory=list)
    deletes: list[tuple[str, str]] = field(default_factory=list)

    def load(self, *, server_origin, account):
        return self.values.get((server_origin, account))

    def save(self, *, server_origin, account, credential):
        self.values[(server_origin, account)] = credential
        self.saves.append((server_origin, account, credential))

    def delete(self, *, server_origin, account):
        self.values.pop((server_origin, account), None)
        self.deletes.append((server_origin, account))


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
        ("https://Study.Example/api/v1", "https://study.example"),
        ("https://study.example:443/base", "https://study.example"),
    ],
)
def test_server_origin_normalization_partitions_credentials(url, origin):
    assert normalize_server_origin(url) == origin


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/server", "https://user:password@example.com", "https://example.com?token=secret", "not a url"],
)
def test_server_origin_rejects_unsafe_values(url):
    with pytest.raises(ValueError):
        normalize_server_origin(url)


def test_sign_out_deletes_only_selected_server_and_account():
    store = MemoryCredentialStore(
        values={
            ("https://one.example", "subject-a"): "credential-a",
            ("https://one.example", "subject-b"): "credential-b",
            ("https://two.example", "subject-a"): "credential-c",
        }
    )

    sign_out(api_base_url="https://one.example/api", account="subject-a", store=store)

    assert ("https://one.example", "subject-a") not in store.values
    assert store.values[("https://one.example", "subject-b")] == "credential-b"
    assert store.values[("https://two.example", "subject-a")] == "credential-c"


def test_missing_stored_credential_requires_sign_in_without_opening_browser():
    store = MemoryCredentialStore()

    with pytest.raises(DesktopAuthError, match="(?i)sign in"):
        load_authenticated_account(
            api_base_url="https://one.example",
            account="subject-a",
            store=store,
        )


def test_browser_open_failure_is_redacted_and_does_not_store_a_credential():
    store = MemoryCredentialStore()

    with pytest.raises(DesktopAuthError) as caught:
        sign_in(
            api_base_url="https://one.example",
            timeout_seconds=5,
            store=store,
            open_browser=lambda _: False,
        )

    assert store.saves == []
    assert "credential" not in str(caught.value).lower()


def test_client_settings_do_not_contain_server_oauth_secrets(monkeypatch):
    from sra.config import ClientSettings

    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "must-stay-server-side")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "also-server-side")
    settings = ClientSettings(api_base_url="http://127.0.0.1:8000")

    dumped = settings.model_dump()
    assert "google_client_secret" not in dumped
    assert "auth_signing_secret" not in dumped
    assert "must-stay-server-side" not in repr(settings)
    assert "also-server-side" not in repr(settings)


def test_real_loopback_binds_localhost_accepts_one_expected_callback_and_releases_port():
    observed = {}

    def open_browser(login_url):
        observed["login_url"] = login_url
        port = int(parse_qs(urlsplit(login_url).query)["port"][0])
        observed["port"] = port

        def callback():
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/callback?completion_code=one-use-code&state=signed-state",
                timeout=2,
            ) as response:
                observed["page"] = response.read().decode()

        thread = threading.Thread(target=callback)
        thread.start()
        observed["thread"] = thread
        return True

    try:
        result = receive_loopback_completion(
            timeout_seconds=2,
            open_browser=open_browser,
            build_login_url=lambda port: f"https://study.example/login?client=desktop&port={port}",
        )
    except DesktopAuthError as error:
        if isinstance(error.__cause__, PermissionError):
            pytest.skip("This sandbox does not permit binding a localhost socket")
        raise

    observed["thread"].join(timeout=2)
    assert result == LoopbackResult(completion_code="one-use-code", state="signed-state")
    assert "one-use-code" not in observed["login_url"]
    assert "completed" in observed["page"].lower()
    with pytest.raises(OSError):
        import socket

        probe = socket.create_connection(("127.0.0.1", observed["port"]), timeout=0.1)
        probe.close()


def test_sign_in_exchanges_once_validates_and_only_then_stores(monkeypatch):
    store = MemoryCredentialStore()
    requests = []
    responses = iter(
        [
            httpx.Response(200, json={"credential": "app-secret", "token_type": "Bearer", "expires_in": 300}),
            httpx.Response(
                200,
                json={"subject": "subject-a", "email": "a@example.com", "display_name": "User A", "role": "user"},
            ),
        ]
    )

    def send(request):
        requests.append(request)
        response = next(responses)
        response.request = request
        return response

    monkeypatch.setattr(
        auth_client,
        "receive_loopback_completion",
        lambda **kwargs: LoopbackResult(completion_code="short-code", state=kwargs["expected_state"]),
    )
    monkeypatch.setattr(
        auth_client,
        "_create_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(send)),
    )

    account = sign_in(
        api_base_url="https://Study.Example/base/",
        timeout_seconds=5,
        store=store,
        open_browser=lambda _: True,
    )

    assert account.email == "a@example.com"
    assert account.credential == "app-secret"
    assert len(requests) == 2
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/base/api/v1/auth/desktop/exchange"
    assert requests[0].read() == b'{"completion_code":"short-code"}'
    assert requests[1].headers["authorization"] == "Bearer app-secret"
    assert store.saves == [("https://study.example", "subject-a", "app-secret")]
    assert "app-secret" not in repr(account)


def test_rejected_saved_credential_is_deleted_and_redacted(monkeypatch):
    store = MemoryCredentialStore(values={("https://study.example", "subject-a"): "expired-secret"})
    monkeypatch.setattr(
        auth_client,
        "_create_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401, request=request))),
    )

    with pytest.raises(DesktopAuthError, match="(?i)sign in again") as caught:
        load_authenticated_account(api_base_url="https://study.example", account="subject-a", store=store)

    assert store.deletes == [("https://study.example", "subject-a")]
    assert "expired-secret" not in str(caught.value)


def test_keychain_adapter_partitions_origins_without_exposing_secret_in_service(monkeypatch):
    values = {}
    backend = SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, value: values.__setitem__((service, account), value),
        delete_password=lambda service, account: values.pop((service, account)),
    )
    monkeypatch.setattr(auth_client.sys, "platform", "darwin")
    store = MacOSKeychainCredentialStore(backend=backend)

    store.save(server_origin="https://one.example", account="subject-a", credential="app-secret")
    assert store.load(server_origin="https://one.example", account="subject-a") == "app-secret"
    assert store.load_default(server_origin="https://one.example") == "app-secret"
    assert store.load(server_origin="https://two.example", account="subject-a") is None
    assert all("app-secret" not in service for service, _ in values)
    store.delete(server_origin="https://one.example", account="subject-a")
    assert values == {}


def test_sign_in_rejects_another_attempt_before_exchange(monkeypatch):
    store = MemoryCredentialStore()
    monkeypatch.setattr(auth_client, "receive_loopback_completion", lambda **_: LoopbackResult("secret-code", "wrong"))
    with pytest.raises(DesktopAuthError, match="match this attempt"):
        sign_in(api_base_url="https://one.example", timeout_seconds=1, store=store)
    assert store.saves == []


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1])
def test_loopback_requires_finite_positive_timeout(timeout):
    with pytest.raises(DesktopAuthError, match="timeout"):
        receive_loopback_completion(timeout_seconds=timeout, open_browser=lambda _: True, build_login_url=lambda _: "")


@pytest.mark.parametrize("cancel", [False, True])
def test_loopback_stalled_headers_obey_deadline_and_cancellation(cancel):
    cancellation = threading.Event()
    peers = []
    workers = []
    observed = {}

    def open_browser(url):
        port = int(url)
        observed["port"] = port
        peer = socket.create_connection(("127.0.0.1", port), timeout=1)
        peers.append(peer)

        def drip():
            try:
                peer.sendall(b"GET /callback?completion_code=private-code&state=private-state HTTP/1.1\r\n")
                for _ in range(40):
                    peer.sendall(b"X")
                    if cancel:
                        cancellation.set()
                    time.sleep(0.02)
            except OSError:
                pass

        worker = threading.Thread(target=drip)
        workers.append(worker)
        worker.start()
        return True

    started = time.monotonic()
    try:
        with pytest.raises(DesktopAuthError, match="cancelled|timed out"):
            receive_loopback_completion(
                timeout_seconds=0.3 if not cancel else 5,
                open_browser=open_browser, build_login_url=lambda port: port, cancel_event=cancellation,
                expected_state="expected",
            )
        assert time.monotonic() - started < 2
    finally:
        for peer in peers:
            peer.close()
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", observed["port"]), timeout=0.1)
    assert not any(thread.name == "sra-login-deadline" for thread in threading.enumerate())


def test_deleting_inactive_account_preserves_active_other_account_and_restart(monkeypatch):
    values = {}
    backend = SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, value: values.__setitem__((service, account), value),
        delete_password=lambda service, account: values.pop((service, account)),
    )
    monkeypatch.setattr(auth_client.sys, "platform", "darwin")
    store = MacOSKeychainCredentialStore(backend=backend)
    origin = "https://one.example"
    store.save(server_origin=origin, account="a", credential="token-a")
    store.save(server_origin=origin, account="b", credential="token-b")
    store.delete(server_origin=origin, account="a")
    restarted = MacOSKeychainCredentialStore(backend=backend)
    assert restarted.load_default(server_origin=origin) == "token-b"
    assert restarted.load(server_origin=origin, account="b") == "token-b"
    restarted.delete(server_origin=origin, account=restarted._ACTIVE_ACCOUNT)
    assert restarted.load_default(server_origin=origin) is None
    assert restarted.load(server_origin=origin, account="b") == "token-b"


def test_native_keychain_backend_is_selected_without_global_keyring(monkeypatch):
    import sys

    backend = SimpleNamespace(get_password=lambda *_: None)
    monkeypatch.setattr(auth_client.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "keyring.backends.macOS", SimpleNamespace(Keyring=lambda: backend))
    assert MacOSKeychainCredentialStore()._keyring() is backend


def test_cancelled_login_never_stores_late_exchange_result(monkeypatch):
    cancellation = threading.Event()
    store = MemoryCredentialStore()
    monkeypatch.setattr(
        auth_client, "receive_loopback_completion",
        lambda **kwargs: LoopbackResult("short-code", kwargs["expected_state"]),
    )

    def send(request):
        if request.method == "POST":
            return httpx.Response(200, json={"credential": "secret", "token_type": "Bearer", "expires_in": 300})
        cancellation.set()
        return httpx.Response(200, json={"subject": "a", "email": "a@example.com", "display_name": "A"})

    monkeypatch.setattr(auth_client, "_create_http_client", lambda: httpx.Client(transport=httpx.MockTransport(send)))
    with pytest.raises(DesktopAuthError, match="cancelled"):
        sign_in(api_base_url="https://one.example", timeout_seconds=1, store=store, cancel_event=cancellation)
    assert not store.saves
