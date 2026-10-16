"""Google identity and application-credential primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from sra.db.models import User

_CREDENTIAL_ISSUER = "spaced-repetition-agent"
_CREDENTIAL_AUDIENCE = "sra-api"
_CREDENTIAL_PURPOSE = "application-credential"
_CREDENTIAL_REFRESH_PURPOSE = "refresh-credential"


class AuthError(Exception):
    """A safe authentication failure suitable for translation at the API edge."""


class ProviderError(AuthError):
    """A safe failure while communicating with or validating Google."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Provider-verified identity fields used by the application."""

    subject: str
    email: str
    display_name: str


@dataclass(frozen=True)
class CredentialClaims:
    """Trusted claims recovered from a server-issued application credential."""

    user_id: int
    expires_at: datetime


@dataclass(frozen=True)
class RefreshClaims:
    """Trusted claims recovered from a server-issued refresh credential."""

    user_id: int
    jti: str
    expires_at: datetime


@dataclass(frozen=True)
class LoginAttempt:
    """Trusted metadata recovered from a one-use OAuth state value."""

    client: str
    server_origin: str
    loopback_port: int | None = None
    desktop_state: str | None = None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: dict[str, object], secret: str) -> str:
    if not secret:
        raise AuthError("Authentication is not configured")
    encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def _unsign(value: str, secret: str) -> dict[str, Any]:
    try:
        encoded, supplied = value.split(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(supplied), expected):
            raise ValueError
        payload = json.loads(_b64decode(encoded))
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AuthError("Invalid authentication credential") from error


def build_authorization_url(
    *, authorization_endpoint: str, client_id: str, callback_url: str, state: str
) -> str:
    """Build Google's consent URL using only identity scopes."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
    )
    return f"{authorization_endpoint}?{query}"


def verify_google_identity(
    identity_token: str,
    *,
    expected_audience: str,
    verifier: Callable[[str, str], dict[str, Any]],
) -> GoogleIdentity:
    """Validate the verified claim shapes and return the stable Google subject."""
    try:
        claims = verifier(identity_token, expected_audience)
    except Exception as error:
        raise ProviderError("Google identity verification failed") from error

    subject = claims.get("sub")
    email = claims.get("email")
    display_name = claims.get("name")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError("Google returned an incomplete identity")
    if not isinstance(email, str) or not email.strip():
        raise AuthError("Google returned an incomplete identity")
    if not isinstance(display_name, str) or not display_name.strip():
        raise AuthError("Google returned an incomplete identity")
    return GoogleIdentity(subject.strip(), email.strip(), display_name.strip())


def upsert_google_user(identity: GoogleIdentity, session: Session, *, now: datetime) -> User:
    """Create or update a user by stable subject while preserving role and ownership."""
    user = session.exec(select(User).where(User.google_sub == identity.subject)).first()
    if user is None:
        user = User(
            google_sub=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
            last_login_at=now,
        )
    else:
        user.email = identity.email
        user.display_name = identity.display_name
        user.last_login_at = now

    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise AuthError("The verified Google identity cannot be linked") from error
    session.refresh(user)
    if user.last_login_at is not None and user.last_login_at.tzinfo is None:
        user.last_login_at = user.last_login_at.replace(tzinfo=UTC)
    return user


def issue_app_credential(
    user_id: int, *, signing_secret: str, ttl_seconds: int, now: datetime
) -> str:
    """Issue a signed, issuer/audience/purpose-bound credential."""
    if user_id <= 0 or ttl_seconds <= 0:
        raise AuthError("Invalid application credential parameters")
    issued_at = int(now.timestamp())
    # The random nonce also lets us guarantee the serialized credential does
    # not visibly contain its numeric database subject.
    while True:
        credential = _sign(
            {
                "iss": _CREDENTIAL_ISSUER,
                "aud": _CREDENTIAL_AUDIENCE,
                "purpose": _CREDENTIAL_PURPOSE,
                "sub": user_id,
                "iat": issued_at,
                "exp": issued_at + ttl_seconds,
                "nonce": secrets.token_urlsafe(12),
            },
            signing_secret,
        )
        # A one-digit string appears naturally throughout any compact token;
        # for multi-digit subjects, avoid the misleading appearance of a
        # plaintext database ID in the opaque value.
        if user_id < 10 or str(user_id) not in credential:
            return credential


def verify_app_credential(
    credential: str, *, signing_secret: str, now: datetime
) -> CredentialClaims:
    """Verify signature, fixed claims, subject, and expiry."""
    payload = _unsign(credential, signing_secret)
    try:
        subject = payload["sub"]
        expires = payload["exp"]
        valid = (
            payload["iss"] == _CREDENTIAL_ISSUER
            and payload["aud"] == _CREDENTIAL_AUDIENCE
            and payload["purpose"] == _CREDENTIAL_PURPOSE
            and isinstance(subject, int)
            and not isinstance(subject, bool)
            and subject > 0
            and isinstance(expires, int)
            and expires > int(now.timestamp())
        )
    except (KeyError, TypeError):
        valid = False
        subject = 0
        expires = 0
    if not valid:
        raise AuthError("Invalid or expired authentication credential")
    return CredentialClaims(user_id=subject, expires_at=datetime.fromtimestamp(expires, tz=UTC))


def issue_refresh_credential(
    user_id: int,
    jti: str,
    *,
    signing_secret: str,
    ttl_seconds: int,
    now: datetime,
) -> str:
    """Issue a signed refresh credential bound to one user and one jti.

    The refresh credential's purpose is distinct from the access credential so
    the access verifier rejects it and the refresh verifier rejects an access
    credential. The random nonce avoids the user_id appearing in plaintext
    inside the opaque serialized value.
    """
    if user_id <= 0 or ttl_seconds <= 0 or not jti or not jti.strip():
        raise AuthError("Invalid refresh credential parameters")
    issued_at = int(now.timestamp())
    while True:
        credential = _sign(
            {
                "iss": _CREDENTIAL_ISSUER,
                "aud": _CREDENTIAL_AUDIENCE,
                "purpose": _CREDENTIAL_REFRESH_PURPOSE,
                "sub": user_id,
                "jti": jti,
                "iat": issued_at,
                "exp": issued_at + ttl_seconds,
                "nonce": secrets.token_urlsafe(12),
            },
            signing_secret,
        )
        if user_id < 10 or str(user_id) not in credential:
            return credential


def verify_refresh_credential(
    credential: str, *, signing_secret: str, now: datetime
) -> RefreshClaims:
    """Verify signature, fixed claims, jti, subject, and expiry.

    Rejects an access credential (different ``purpose``) so a refresh credential
    is the only thing that satisfies :func:`require_refresh_token`.
    """
    payload = _unsign(credential, signing_secret)
    try:
        subject = payload["sub"]
        jti = payload["jti"]
        expires = payload["exp"]
        valid = (
            payload["iss"] == _CREDENTIAL_ISSUER
            and payload["aud"] == _CREDENTIAL_AUDIENCE
            and payload["purpose"] == _CREDENTIAL_REFRESH_PURPOSE
            and isinstance(subject, int)
            and not isinstance(subject, bool)
            and subject > 0
            and isinstance(jti, str)
            and bool(jti)
            and isinstance(expires, int)
            and expires > int(now.timestamp())
        )
    except (KeyError, TypeError):
        valid = False
        subject = 0
        jti = ""
        expires = 0
    if not valid:
        raise AuthError("Invalid or expired authentication credential")
    return RefreshClaims(
        user_id=subject,
        jti=jti,
        expires_at=datetime.fromtimestamp(expires, tz=UTC),
    )


class CompletionCodeStore:
    """In-memory owner of short-lived, one-use desktop completion codes."""

    def __init__(self, *, ttl_seconds: int, clock: Callable[[], datetime]) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._codes: dict[str, tuple[int, str, datetime]] = {}
        self._lock = Lock()

    def issue(self, *, user_id: int, server_origin: str) -> str:
        """Return an opaque code bound to one user and server origin."""
        with self._lock:
            self._discard_expired()
            code = secrets.token_urlsafe(32)
            self._codes[code] = (user_id, server_origin, self.clock() + timedelta(seconds=self.ttl_seconds))
        return code

    def exchange(self, code: str, *, server_origin: str) -> int:
        """Consume a code exactly once, including on a mismatched exchange."""
        with self._lock:
            record = self._codes.pop(code, None)
        if record is None:
            raise AuthError("Invalid or expired completion code")
        user_id, expected_origin, expires_at = record
        if not hmac.compare_digest(expected_origin, server_origin) or self.clock() >= expires_at:
            raise AuthError("Invalid or expired completion code")
        return user_id

    def _discard_expired(self) -> None:
        now = self.clock()
        self._codes = {code: record for code, record in self._codes.items() if record[2] > now}


class RefreshTokenStore:
    """In-memory owner of long-lived, single-use refresh-token jti values.

    Mirrors :class:`OAuthStateStore` and :class:`CompletionCodeStore`: a Lock
    guards the dict, the supplied clock drives expiry, and a single exchange
    always deletes the record (even on a wrong-origin mismatch, so a replay
    cannot be observed). A second submission of a consumed ``jti`` is detected
    as a reuse of a rotated token; in that case the whole refresh family for
    that ``user_id`` is revoked to invalidate a leaked credential before the
    next legitimate refresh.
    """

    def __init__(self, *, ttl_seconds: int, clock: Callable[[], datetime]) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._jtis: dict[str, tuple[int, str, datetime]] = {}
        self._lock = Lock()

    def issue(self, *, user_id: int, server_origin: str) -> str:
        """Return a fresh, single-use jti bound to one user and server origin."""
        with self._lock:
            self._discard_expired()
            jti = secrets.token_urlsafe(24)
            self._jtis[jti] = (user_id, server_origin, self.clock() + timedelta(seconds=self.ttl_seconds))
        return jti

    def consume(self, jti: str, *, server_origin: str) -> tuple[int, str]:
        """Atomically consume the jti; raise on mismatch, expiry, or replay.

        A successful consume returns ``(user_id, server_origin)`` and removes
        the jti from the store. A failed consume also removes the jti so a
        second presentation is always rejected, which is the trigger for
        :meth:`revoke_family` to invalidate the leaked chain.
        """
        with self._lock:
            record = self._jtis.pop(jti, None)
        if record is None:
            raise AuthError("Invalid or expired refresh token")
        user_id, expected_origin, expires_at = record
        if (
            not hmac.compare_digest(expected_origin, server_origin)
            or self.clock() >= expires_at
        ):
            raise AuthError("Invalid or expired refresh token")
        return user_id, server_origin

    def revoke_family(self, user_id: int) -> int:
        """Revoke every active jti for one user. Returns the number removed."""
        with self._lock:
            self._discard_expired()
            removed = 0
            for jti, record in list(self._jtis.items()):
                if record[0] == user_id:
                    del self._jtis[jti]
                    removed += 1
        return removed

    def _discard_expired(self) -> None:
        now = self.clock()
        self._jtis = {jti: record for jti, record in self._jtis.items() if record[2] > now}


class OAuthStateStore:
    """Signed, expiring, one-use OAuth state values with replay protection."""

    def __init__(self, *, signing_secret: str, ttl_seconds: int, clock: Callable[[], datetime]) -> None:
        self.signing_secret = signing_secret
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._nonces: dict[str, int] = {}
        self._lock = Lock()

    def issue(self, attempt: LoginAttempt) -> str:
        nonce = secrets.token_urlsafe(24)
        now = int(self.clock().timestamp())
        with self._lock:
            self._nonces = {key: expiry for key, expiry in self._nonces.items() if expiry > now}
            self._nonces[nonce] = now + self.ttl_seconds
        return _sign(
            {
                "purpose": "oauth-state",
                "nonce": nonce,
                "client": attempt.client,
                "server_origin": attempt.server_origin,
                "loopback_port": attempt.loopback_port,
                "desktop_state": attempt.desktop_state,
                "exp": now + self.ttl_seconds,
            },
            self.signing_secret,
        )

    def consume(self, state: str) -> LoginAttempt:
        payload = _unsign(state, self.signing_secret)
        nonce = payload.get("nonce")
        if not isinstance(nonce, str):
            raise AuthError("Invalid or expired OAuth state")
        with self._lock:
            if self._nonces.pop(nonce, None) is None:
                raise AuthError("Invalid or expired OAuth state")
        client = payload.get("client")
        origin = payload.get("server_origin")
        port = payload.get("loopback_port")
        if (
            payload.get("purpose") != "oauth-state"
            or not isinstance(payload.get("exp"), int)
            or payload["exp"] <= int(self.clock().timestamp())
            or client not in {"web", "desktop"}
            or not isinstance(origin, str)
            or (port is not None and (not isinstance(port, int) or isinstance(port, bool)))
        ):
            raise AuthError("Invalid or expired OAuth state")
        desktop_state = payload.get("desktop_state")
        if desktop_state is not None and not isinstance(desktop_state, str):
            raise AuthError("Invalid or expired OAuth state")
        return LoginAttempt(client=client, server_origin=origin, loopback_port=port, desktop_state=desktop_state)
