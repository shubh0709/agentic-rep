"""Google login, application sessions, and desktop credential exchange."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session

from sra.api.deps import get_session, require_user
from sra.api.rate_limit import limit_anonymous, limit_authenticated
from sra.auth.google_oauth import (
    AuthError,
    CompletionCodeStore,
    GoogleIdentity,
    LoginAttempt,
    OAuthStateStore,
    ProviderError,
    RefreshTokenStore,
    build_authorization_url,
    issue_app_credential,
    issue_refresh_credential,
    upsert_google_user,
    verify_google_identity,
    verify_refresh_credential,
)
from sra.config import Settings, get_settings
from sra.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

_state_store: OAuthStateStore | None = None
_state_store_config: tuple[str, int] | None = None
_completion_store: CompletionCodeStore | None = None
_completion_ttl: int | None = None
_refresh_store: RefreshTokenStore | None = None
_refresh_store_config: tuple[str, int] | None = None

REFRESH_COOKIE_NAME = "sra_refresh"


class _RefreshStoreResolver(Protocol):
    """Minimal interface the auth router needs from the refresh store."""


class DesktopExchangeRequest(BaseModel):
    completion_code: str


class CredentialResponse(BaseModel):
    credential: str
    refresh_credential: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_expires_in: int


class UserResponse(BaseModel):
    subject: str
    email: str
    display_name: str
    role: str


def _clock() -> datetime:
    return datetime.now(UTC)


def _server_origin(settings: Settings) -> str:
    parsed = urlsplit(settings.google_callback_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _refresh_cookie_name(settings: Settings) -> str:
    return REFRESH_COOKIE_NAME


def _stores(
    settings: Settings,
) -> tuple[OAuthStateStore, CompletionCodeStore, RefreshTokenStore]:
    global _completion_store, _completion_ttl, _state_store, _state_store_config
    global _refresh_store, _refresh_store_config
    secret = settings.auth_signing_secret.get_secret_value()
    state_config = (secret, settings.oauth_state_ttl_seconds)
    if _state_store is None or _state_store_config != state_config:
        _state_store = OAuthStateStore(
            signing_secret=secret,
            ttl_seconds=settings.oauth_state_ttl_seconds,
            clock=_clock,
        )
        _state_store_config = state_config
    if _completion_store is None or _completion_ttl != settings.desktop_completion_ttl_seconds:
        _completion_store = CompletionCodeStore(
            ttl_seconds=settings.desktop_completion_ttl_seconds,
            clock=_clock,
        )
        _completion_ttl = settings.desktop_completion_ttl_seconds
    refresh_config = (secret, settings.auth_refresh_ttl_seconds)
    if _refresh_store is None or _refresh_store_config != refresh_config:
        _refresh_store = RefreshTokenStore(
            ttl_seconds=settings.auth_refresh_ttl_seconds,
            clock=_clock,
        )
        _refresh_store_config = refresh_config
    return _state_store, _completion_store, _refresh_store


def _issue_refresh_pair(
    *, user_id: int, server_origin: str, settings: Settings
) -> tuple[str, str, int, int]:
    """Mint a fresh (refresh_credential, access_credential, expires_in, refresh_expires_in) tuple."""
    _, _, refresh_store = _stores(settings)
    now = _clock()
    jti = refresh_store.issue(user_id=user_id, server_origin=server_origin)
    refresh_credential = issue_refresh_credential(
        user_id,
        jti,
        signing_secret=settings.auth_signing_secret.get_secret_value(),
        ttl_seconds=settings.auth_refresh_ttl_seconds,
        now=now,
    )
    access_credential = issue_app_credential(
        user_id,
        signing_secret=settings.auth_signing_secret.get_secret_value(),
        ttl_seconds=settings.auth_credential_ttl_seconds,
        now=now,
    )
    return (
        access_credential,
        refresh_credential,
        settings.auth_credential_ttl_seconds,
        settings.auth_refresh_ttl_seconds,
    )


def _set_session_cookies(
    response: Response,
    *,
    settings: Settings,
    access_credential: str,
    refresh_credential: str,
) -> None:
    response.set_cookie(
        settings.auth_cookie_name,
        access_credential,
        max_age=settings.auth_credential_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        _refresh_cookie_name(settings),
        refresh_credential,
        max_age=settings.auth_refresh_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def _clear_session_cookies(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
    response.delete_cookie(
        _refresh_cookie_name(settings),
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def _google_verifier(identity_token: str, audience: str) -> dict[str, object]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return dict(id_token.verify_oauth2_token(identity_token, google_requests.Request(), audience))


def exchange_google_code(code: str, settings: Settings) -> GoogleIdentity:
    """Exchange a one-time code and verify Google's returned identity token."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                settings.google_token_endpoint,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret.get_secret_value(),
                    "redirect_uri": settings.google_callback_url,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            payload = response.json()
        token = payload.get("id_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("Google did not return a verifiable identity")
        return verify_google_identity(
            token,
            expected_audience=settings.google_client_id,
            verifier=_google_verifier,
        )
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise ProviderError("Google sign-in is temporarily unavailable") from error


@router.get("/google/login", dependencies=[Depends(limit_anonymous)])
def google_login(
    request: Request,
    client: str = Query(default="web", pattern="^(web|desktop)$"),
    port: int | None = Query(default=None, ge=1, le=65535),
    desktop_state: str | None = Query(default=None, min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Start a web or loopback desktop login with protected state."""
    del request  # Host headers never decide the credential's server binding.
    try:
        settings.require_auth_configuration()
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    if (client == "desktop") != (port is not None):
        raise HTTPException(status_code=400, detail="Desktop login requires one loopback port")
    if (client == "desktop") != (desktop_state is not None):
        raise HTTPException(status_code=400, detail="Desktop login requires an attempt binding")
    state_store, _, _ = _stores(settings)
    state_value = state_store.issue(
        LoginAttempt(
            client=client, loopback_port=port, server_origin=_server_origin(settings), desktop_state=desktop_state,
        )
    )
    url = build_authorization_url(
        authorization_endpoint=settings.google_authorization_endpoint,
        client_id=settings.google_client_id,
        callback_url=settings.google_callback_url,
        state=state_value,
    )
    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    if client == "web":
        response.set_cookie(
            f"{settings.auth_cookie_name}_login", state_value, max_age=settings.oauth_state_ttl_seconds,
            httponly=True, secure=settings.auth_cookie_secure, samesite="lax", path="/api/v1/auth/google/callback",
        )
    return response


@router.get("/google/callback", dependencies=[Depends(limit_anonymous)])
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Consume state before provider exchange, then establish the chosen transport."""
    if not code or not state:
        raise HTTPException(status_code=400, detail="Invalid or expired login attempt")
    try:
        settings.require_auth_configuration()
        state_store, completion_store, _ = _stores(settings)
        attempt = state_store.consume(state)
        if attempt.client == "web" and not hmac.compare_digest(
            request.cookies.get(f"{settings.auth_cookie_name}_login", ""), state,
        ):
            raise AuthError("Login attempt belongs to another browser")
    except (AuthError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid or expired login attempt") from error
    try:
        identity = exchange_google_code(code, settings)
        user = upsert_google_user(identity, session, now=_clock())
    except AuthError as error:
        raise HTTPException(status_code=502, detail="Google sign-in could not be completed") from error
    assert user.id is not None

    if attempt.client == "desktop":
        assert attempt.loopback_port is not None
        completion = completion_store.issue(user_id=user.id, server_origin=attempt.server_origin)
        query = urlencode({"completion_code": completion, "state": attempt.desktop_state})
        return RedirectResponse(f"http://127.0.0.1:{attempt.loopback_port}/callback?{query}", status_code=302)

    access_credential, refresh_credential, _, _ = _issue_refresh_pair(
        user_id=user.id, server_origin=attempt.server_origin, settings=settings,
    )
    response = RedirectResponse(settings.auth_web_redirect_url, status_code=302)
    response.delete_cookie(
        f"{settings.auth_cookie_name}_login", path="/api/v1/auth/google/callback",
        httponly=True, secure=settings.auth_cookie_secure, samesite="lax",
    )
    _set_session_cookies(
        response,
        settings=settings,
        access_credential=access_credential,
        refresh_credential=refresh_credential,
    )
    return response


@router.post("/desktop/exchange", response_model=CredentialResponse, dependencies=[Depends(limit_anonymous)])
def desktop_exchange(
    body: DesktopExchangeRequest,
    settings: Settings = Depends(get_settings),
) -> CredentialResponse:
    """Exchange a server-bound one-use code for a bearer credential pair."""
    try:
        settings.require_auth_configuration()
        _, completion_store, _ = _stores(settings)
        user_id = completion_store.exchange(body.completion_code, server_origin=_server_origin(settings))
    except (AuthError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid or expired completion code") from error
    access_credential, refresh_credential, access_ttl, refresh_ttl = _issue_refresh_pair(
        user_id=user_id, server_origin=_server_origin(settings), settings=settings,
    )
    return CredentialResponse(
        credential=access_credential,
        refresh_credential=refresh_credential,
        expires_in=access_ttl,
        refresh_expires_in=refresh_ttl,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(limit_anonymous)])
def logout(settings: Settings = Depends(get_settings)) -> Response:
    """Clear the browser session cookies (access and refresh)."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookies(response, settings=settings)
    return response


def _resolve_refresh_credential(request: Request, settings: Settings) -> str | None:
    cookie = request.cookies.get(_refresh_cookie_name(settings))
    if cookie:
        return cookie
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return None
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


@router.post(
    "/refresh",
    response_model=CredentialResponse,
    dependencies=[Depends(limit_anonymous)],
)
def refresh(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> CredentialResponse:
    """Exchange a valid refresh credential for a new access + refresh pair.

    The submitted refresh credential is consumed atomically: a single-use jti
    cannot be replayed. A second submission of an already-consumed jti
    indicates a leaked credential, so the entire refresh family for that user
    is revoked and the call returns ``401`` to force a fresh sign-in.
    """
    try:
        settings.require_auth_configuration()
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error),
        ) from error
    refresh_value = _resolve_refresh_credential(request, settings)
    if not refresh_value:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = verify_refresh_credential(
            refresh_value,
            signing_secret=settings.auth_signing_secret.get_secret_value(),
            now=_clock(),
        )
        _, _, refresh_store = _stores(settings)
        user_id, server_origin = refresh_store.consume(claims.jti, server_origin=_server_origin(settings))
    except AuthError:
        # Either the credential is invalid/expired or the jti was already
        # consumed. Revoke the whole family so a stolen token cannot be
        # replayed, and tell the caller to sign in again.
        try:
            claims = verify_refresh_credential(
                refresh_value,
                signing_secret=settings.auth_signing_secret.get_secret_value(),
                now=_clock(),
            )
            _, _, refresh_store = _stores(settings)
            refresh_store.revoke_family(claims.user_id)
        except AuthError:
            pass
        raise HTTPException(status_code=401, detail="Sign in again to continue") from None

    access_credential, new_refresh_credential, access_ttl, refresh_ttl = _issue_refresh_pair(
        user_id=user_id, server_origin=server_origin, settings=settings,
    )

    _set_session_cookies(
        response,
        settings=settings,
        access_credential=access_credential,
        refresh_credential=new_refresh_credential,
    )
    return CredentialResponse(
        credential=access_credential,
        refresh_credential=new_refresh_credential,
        expires_in=access_ttl,
        refresh_expires_in=refresh_ttl,
    )


@router.get("/me", response_model=UserResponse, dependencies=[Depends(limit_authenticated)])
def get_me(user: User = Depends(require_user)) -> UserResponse:
    """Return a safe account shape without credentials or provider tokens."""
    assert user.google_sub is not None
    return UserResponse(
        subject=user.google_sub,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
    )
