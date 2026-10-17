"""Desktop OAuth orchestration and macOS Keychain credential storage."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ValidationError

AUTH_HTTP_TIMEOUT_SECONDS = 15.0
LOOPBACK_PATH = "/callback"


class DesktopAuthError(Exception):
    """A redacted, user-facing desktop sign-in failure."""


class CredentialStore(Protocol):
    """Minimal secure-storage boundary implemented by macOS Keychain."""

    def load(self, *, server_origin: str, account: str) -> str | None: ...

    def save(self, *, server_origin: str, account: str, credential: str) -> None: ...

    def save_pair(
        self,
        *,
        server_origin: str,
        account: str,
        credential: str,
        refresh_credential: str,
    ) -> None: ...

    def delete(self, *, server_origin: str, account: str) -> None: ...


@dataclass(frozen=True)
class AuthenticatedAccount:
    """Safe account details returned by ``/auth/me`` plus its borrowed token."""

    subject: str
    email: str
    display_name: str
    credential: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class LoopbackResult:
    """The only values accepted from the browser callback."""

    completion_code: str = field(repr=False)
    state: str = field(repr=False)


def normalize_server_origin(api_base_url: str) -> str:
    """Return a stable HTTP(S) origin used to partition Keychain entries."""
    if "\\" in api_base_url or any(character.isspace() or ord(character) < 32 for character in api_base_url):
        raise ValueError("Server URL contains unsafe characters")
    parsed = urlsplit(api_base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Server URL must be an HTTP(S) URL without credentials, query, or fragment")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Server URL contains an invalid port") from error
    if port == 0:
        raise ValueError("Server URL contains an invalid port")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    authority = host if port is None or default_port else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


class _AccountResponse(BaseModel):
    subject: str
    email: str
    display_name: str


class _ExchangeResponse(BaseModel):
    credential: str
    token_type: str
    expires_in: int


def _create_http_client() -> httpx.Client:
    return httpx.Client(timeout=AUTH_HTTP_TIMEOUT_SECONDS, follow_redirects=False)


def _browser_open(url: str) -> bool:
    return webbrowser.open(url, new=1, autoraise=True)


class _LoopbackServer(HTTPServer):
    result: LoopbackResult | None
    callback_error: str | None
    expected_state: str | None = None
    active_socket: socket.socket | None = None

    def get_request(self) -> tuple[socket.socket, object]:
        connection, address = super().get_request()
        self.active_socket = connection
        connection.settimeout(0.2)
        return connection, address

    def handle_error(self, request: object, client_address: object) -> None:
        # Broken local connections must not print callback URLs or tracebacks.
        return


class _LoopbackHandler(BaseHTTPRequestHandler):
    server: _LoopbackServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query, keep_blank_values=True)
        codes = values.get("completion_code", [])
        states = values.get("state", [])
        if parsed.path != LOOPBACK_PATH or len(codes) != 1 or len(states) != 1 or not codes[0] or not states[0]:
            self.server.callback_error = "The sign-in callback was malformed. Please try again."
            self._reply(400, "Sign-in could not be completed. You may close this window and try again.")
            return
        if self.server.expected_state is not None and not hmac.compare_digest(states[0], self.server.expected_state):
            self.server.callback_error = "The sign-in callback did not match this attempt. Please try again."
            self._reply(400, "Sign-in could not be completed. Please try again.")
            return
        self.server.result = LoopbackResult(completion_code=codes[0], state=states[0])
        self._reply(200, "Sign-in completed. You may close this window and return to the app.")

    def _reply(self, status: int, message: str) -> None:
        body = (
            "<!doctype html><html><head><meta charset='utf-8'><title>Spaced Repetition Agent</title></head>"
            f"<body><p>{message}</p></body></html>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # The default logger includes the callback URL and its one-use code.
        return


def _create_loopback_server() -> _LoopbackServer:
    server = _LoopbackServer(("127.0.0.1", 0), _LoopbackHandler, bind_and_activate=False)
    server.result = None
    server.callback_error = None
    try:
        server.server_bind()
        server.server_activate()
    except Exception:
        server.server_close()
        raise
    return server


def receive_loopback_completion(
    *,
    timeout_seconds: float,
    open_browser: Callable[[str], bool],
    build_login_url: Callable[[str], str],
    expected_state: str | None = None,
    cancel_event: threading.Event | None = None,
) -> LoopbackResult:
    """Receive one completion on an ephemeral ``127.0.0.1`` listener.

    Bind before opening the browser, accept only the expected path, enforce the
    timeout, and close the listener on success and every failure.  The loopback
    URL may contain a completion code but never the bearer credential.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise DesktopAuthError("The desktop sign-in timeout must be positive.")
    if cancel_event is not None and cancel_event.is_set():
        raise DesktopAuthError("Sign-in cancelled.")
    deadline = time.monotonic() + timeout_seconds
    stopped = threading.Event()

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def interrupt_connection() -> None:
        # A client drip-feeding headers must not extend the absolute deadline.
        while not stopped.wait(0.05):
            if cancelled() or time.monotonic() >= deadline:
                connection = server.active_socket
                if connection is not None:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return

    try:
        server = _create_loopback_server()
    except OSError as error:
        raise DesktopAuthError("Could not start the local sign-in receiver.") from error
    watcher = threading.Thread(
        target=interrupt_connection,
        name="sra-login-deadline",
        daemon=True,
    )
    watcher.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        if host != "127.0.0.1":
            raise DesktopAuthError("The local sign-in receiver was not bound safely.")
        server.timeout = min(0.1, timeout_seconds)
        server.expected_state = expected_state
        login_url = build_login_url(str(port))
        try:
            opened = open_browser(login_url)
        except Exception:
            raise DesktopAuthError("Could not open the browser for sign-in.") from None
        if not opened:
            raise DesktopAuthError("Could not open the browser for sign-in.")
        try:
            while server.result is None and server.callback_error is None:
                if cancelled():
                    raise DesktopAuthError("Sign-in cancelled.")
                if time.monotonic() >= deadline:
                    break
                server.handle_request()
        except OSError as error:
            raise DesktopAuthError("The browser sign-in did not complete. Please try again.") from error
        if cancelled():
            raise DesktopAuthError("Sign-in cancelled.")
        if time.monotonic() >= deadline:
            raise DesktopAuthError("The browser sign-in timed out. Please try again.")
        if server.callback_error is not None:
            raise DesktopAuthError(server.callback_error)
        if server.result is None:
            raise DesktopAuthError("The browser sign-in timed out. Please try again.")
        return server.result
    finally:
        stopped.set()
        watcher.join()
        server.server_close()


def _auth_url(api_base_url: str, path: str) -> str:
    return f"{api_base_url.rstrip('/')}/api/v1/auth/{path}"


def _safe_http_error(action: str, error: Exception) -> DesktopAuthError:
    if isinstance(error, httpx.TimeoutException):
        return DesktopAuthError(f"{action} timed out. Please try again.")
    return DesktopAuthError(f"{action} could not reach the server. Please try again.")


def _validate_credential(api_base_url: str, credential: str) -> AuthenticatedAccount:
    try:
        with _create_http_client() as client:
            response = client.get(
                _auth_url(api_base_url, "me"),
                headers={"Authorization": f"Bearer {credential}"},
            )
    except httpx.RequestError as error:
        raise _safe_http_error("Account validation", error) from None
    if response.status_code in {401, 403}:
        raise DesktopAuthError("Your sign-in is no longer valid. Please sign in again.")
    if response.status_code != 200:
        raise DesktopAuthError(f"Account validation failed with HTTP {response.status_code}. Please try again.")
    try:
        account = _AccountResponse.model_validate(response.json(), strict=True)
    except (ValueError, ValidationError):
        raise DesktopAuthError("The server returned an invalid account response.") from None
    if not account.subject or not account.email or not account.display_name:
        raise DesktopAuthError("The server returned an incomplete account response.")
    return AuthenticatedAccount(**account.model_dump(), credential=credential)


def sign_in(
    *,
    api_base_url: str,
    timeout_seconds: float,
    store: CredentialStore,
    open_browser: Callable[[str], bool] = _browser_open,
    cancel_event: threading.Event | None = None,
) -> AuthenticatedAccount:
    """Complete browser login, exchange once, validate, and store credential."""
    origin = normalize_server_origin(api_base_url)
    expected_state = secrets.token_urlsafe(32)

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DesktopAuthError("Sign-in cancelled.")

    def build_login_url(port: str) -> str:
        query = urlencode({"client": "desktop", "port": port, "desktop_state": expected_state})
        return f"{_auth_url(api_base_url, 'google/login')}?{query}"

    completion = receive_loopback_completion(
        timeout_seconds=timeout_seconds,
        open_browser=open_browser,
        build_login_url=build_login_url,
        expected_state=expected_state,
        cancel_event=cancel_event,
    )
    check_cancelled()
    if not hmac.compare_digest(completion.state, expected_state):
        raise DesktopAuthError("The sign-in callback did not match this attempt. Please try again.")
    try:
        with _create_http_client() as client:
            response = client.post(
                _auth_url(api_base_url, "desktop/exchange"),
                json={"completion_code": completion.completion_code},
            )
    except httpx.RequestError as error:
        raise _safe_http_error("Credential exchange", error) from None
    if response.status_code != 200:
        raise DesktopAuthError(f"Sign-in exchange failed with HTTP {response.status_code}. Please sign in again.")
    try:
        exchange = _ExchangeResponse.model_validate(response.json(), strict=True)
    except (ValueError, ValidationError):
        raise DesktopAuthError("The server returned an invalid sign-in response.") from None
    if exchange.token_type.lower() != "bearer" or not exchange.credential or exchange.expires_in <= 0:
        raise DesktopAuthError("The server returned an invalid sign-in response.")
    check_cancelled()
    account = _validate_credential(api_base_url, exchange.credential)
    check_cancelled()
    try:
        store.save(server_origin=origin, account=account.subject, credential=exchange.credential)
    except Exception:
        raise DesktopAuthError("Sign-in succeeded, but the credential could not be saved securely.") from None
    return account


def load_authenticated_account(
    *,
    api_base_url: str,
    account: str,
    store: CredentialStore,
) -> AuthenticatedAccount:
    """Load a credential and validate it against the originating ``/auth/me``.

    Missing, expired, rejected, or wrong-server credentials must become a safe
    sign-in-required error.  Delete unusable stored credentials where safe.
    """
    origin = normalize_server_origin(api_base_url)
    try:
        credential = store.load(server_origin=origin, account=account)
    except Exception:
        raise DesktopAuthError("The saved sign-in could not be read securely. Please sign in again.") from None
    if not credential:
        raise DesktopAuthError("No saved sign-in was found. Please sign in again.")
    try:
        authenticated = _validate_credential(api_base_url, credential)
    except DesktopAuthError as error:
        # Rejected credentials cannot recover. Preserve credentials through a
        # temporary transport/server outage instead of unexpectedly signing out.
        if "sign in again" in str(error).lower():
            try:
                store.delete(server_origin=origin, account=account)
            except Exception:
                pass
        raise
    if authenticated.subject != account:
        try:
            store.delete(server_origin=origin, account=account)
        except Exception:
            pass
        raise DesktopAuthError("The saved sign-in belongs to another account. Please sign in again.")
    return authenticated


def sign_out(*, api_base_url: str, account: str, store: CredentialStore) -> None:
    """Remove only this server/account credential from secure storage."""
    try:
        store.delete(server_origin=normalize_server_origin(api_base_url), account=account)
    except Exception:
        raise DesktopAuthError("The saved sign-in could not be removed securely.") from None


class MacOSKeychainCredentialStore:
    """Store application credentials in the user's login Keychain."""

    _ACTIVE_ACCOUNT = "__active_application_credential__"

    def __init__(
        self,
        *,
        service_prefix: str = "com.spaced-repetition-agent.desktop",
        backend: object | None = None,
    ) -> None:
        self._service_prefix = service_prefix
        self._backend = backend

    def _service(self, server_origin: str) -> str:
        digest = hashlib.sha256(server_origin.encode()).hexdigest()
        return f"{self._service_prefix}.{digest}"

    def _keyring(self) -> object:
        if sys.platform != "darwin":
            raise DesktopAuthError("macOS Keychain is available only on macOS.")
        if self._backend is None:
            try:
                from keyring.backends.macOS import Keyring
            except ImportError as error:
                raise DesktopAuthError('Keychain support is missing; install the "desktop" extra.') from error
            # Instantiate the native backend explicitly: the global keyring
            # configuration may select an insecure file or third-party backend.
            self._backend = Keyring()
        return self._backend

    def load(self, *, server_origin: str, account: str) -> str | None:
        try:
            value = self._keyring().get_password(self._service(server_origin), account)  # type: ignore[attr-defined]
        except Exception:
            raise DesktopAuthError("The saved sign-in could not be read securely.") from None
        if value is not None and not isinstance(value, str):
            raise DesktopAuthError("The saved sign-in could not be read securely.")
        return value or None

    def save(self, *, server_origin: str, account: str, credential: str) -> None:
        try:
            backend = self._keyring()
            service = self._service(server_origin)
            backend.set_password(service, account, credential)  # type: ignore[attr-defined]
            # Keyring does not enumerate account names on macOS. A second
            # server-partitioned entry containing the same application
            # credential lets a restarted client discover and validate the
            # last active account through /auth/me without persisting a token
            # or account selector in a config file.
            backend.set_password(service, self._ACTIVE_ACCOUNT, credential)  # type: ignore[attr-defined]
        except Exception:
            raise DesktopAuthError("The sign-in could not be saved securely.") from None

    def save_pair(
        self,
        *,
        server_origin: str,
        account: str,
        credential: str,
        refresh_credential: str,
    ) -> None:
        """Persist a rotated access + refresh pair atomically.

        Writes the access credential under the account key and the refresh
        credential under a server-partitioned sibling key so the next call to
        :meth:`load_refresh_credential` recovers it after a process restart.
        Both writes are attempted in sequence; if the second fails the
        caller sees the existing typed :class:`DesktopAuthError`.
        """
        try:
            backend = self._keyring()
            service = self._service(server_origin)
            backend.set_password(service, account, credential)  # type: ignore[attr-defined]
            backend.set_password(service, self._ACTIVE_ACCOUNT, credential)  # type: ignore[attr-defined]
            backend.set_password(service, f"{account}__refresh", refresh_credential)  # type: ignore[attr-defined]
        except Exception:
            raise DesktopAuthError("The refreshed sign-in could not be saved securely.") from None

    def load_refresh_credential(self, *, server_origin: str, account: str) -> str | None:
        try:
            value = self._keyring().get_password(  # type: ignore[attr-defined]
                self._service(server_origin), f"{account}__refresh",
            )
        except Exception:
            raise DesktopAuthError("The saved sign-in could not be read securely.") from None
        if value is not None and not isinstance(value, str):
            raise DesktopAuthError("The saved sign-in could not be read securely.")
        return value or None

    def load_default(self, *, server_origin: str) -> str | None:
        """Load the last active credential for this origin after an app restart."""
        return self.load(server_origin=server_origin, account=self._ACTIVE_ACCOUNT)

    def delete(self, *, server_origin: str, account: str) -> None:
        backend = self._keyring()
        try:
            service = self._service(server_origin)
            credential = backend.get_password(service, account)  # type: ignore[attr-defined]
            active = backend.get_password(service, self._ACTIVE_ACCOUNT)  # type: ignore[attr-defined]
            if credential is not None:
                backend.delete_password(service, account)  # type: ignore[attr-defined]
            if account != self._ACTIVE_ACCOUNT and credential is not None and active == credential:
                backend.delete_password(service, self._ACTIVE_ACCOUNT)  # type: ignore[attr-defined]
        except Exception:
            raise DesktopAuthError("The saved sign-in could not be removed securely.") from None


def load_default_authenticated_account(
    *, api_base_url: str, store: MacOSKeychainCredentialStore
) -> AuthenticatedAccount:
    """Validate the last active Keychain credential for this server origin."""
    origin = normalize_server_origin(api_base_url)
    try:
        credential = store.load_default(server_origin=origin)
    except Exception:
        raise DesktopAuthError("The saved sign-in could not be read securely. Please sign in again.") from None
    if not credential:
        raise DesktopAuthError("No saved sign-in was found. Please sign in again.")
    try:
        return _validate_credential(api_base_url, credential)
    except DesktopAuthError as error:
        if "sign in again" in str(error).lower():
            try:
                store.delete(server_origin=origin, account=store._ACTIVE_ACCOUNT)
            except Exception:
                pass
        raise
