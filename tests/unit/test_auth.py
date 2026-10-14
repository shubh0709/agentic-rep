"""SRA-302 OAuth, credential, identity-upsert, and RBAC primitives."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlmodel import select

from sra.auth.google_oauth import (
    AuthError,
    CompletionCodeStore,
    GoogleIdentity,
    build_authorization_url,
    issue_app_credential,
    upsert_google_user,
    verify_app_credential,
    verify_google_identity,
)
from sra.db.models import User, UserRole

pytestmark = pytest.mark.phase3
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
SECRET = "test-signing-secret-with-enough-entropy"


def test_authorization_url_uses_exact_callback_identity_scopes_and_protected_state():
    url = build_authorization_url(
        authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        client_id="client-id.apps.googleusercontent.com",
        callback_url="https://app.example.test/api/v1/auth/google/callback",
        state="signed opaque state + punctuation",
    )

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["client-id.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["https://app.example.test/api/v1/auth/google/callback"]
    assert query["response_type"] == ["code"]
    assert set(query["scope"][0].split()) == {"openid", "email", "profile"}
    assert query["state"] == ["signed opaque state + punctuation"]


def test_verified_identity_uses_provider_verifier_and_stable_subject():
    calls = []

    def verifier(token, audience):
        calls.append((token, audience))
        return {
            "sub": "stable-google-subject",
            "email": "learner@example.com",
            "name": "Learner Name",
            "iss": "https://accounts.google.com",
        }

    identity = verify_google_identity("provider-token", expected_audience="client-id", verifier=verifier)

    assert identity == GoogleIdentity("stable-google-subject", "learner@example.com", "Learner Name")
    assert calls == [("provider-token", "client-id")]


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"sub": "", "email": "a@example.com", "name": "A"},
        {"sub": "subject", "email": None, "name": "A"},
        {"sub": "subject", "email": "a@example.com", "name": None},
    ],
)
def test_invalid_verified_profile_claims_are_rejected_safely(claims):
    with pytest.raises(AuthError) as caught:
        verify_google_identity("provider-secret-token", expected_audience="client-id", verifier=lambda *_: claims)

    assert "provider-secret-token" not in str(caught.value)


def test_first_verified_login_creates_user_with_ordinary_role(session):
    identity = GoogleIdentity("subject-1", "first@example.com", "First Name")

    user = upsert_google_user(identity, session, now=NOW)

    assert user.id is not None
    assert user.google_sub == identity.subject
    assert user.email == identity.email
    assert user.display_name == identity.display_name
    assert user.role == "user"
    assert user.last_login_at == NOW


def test_repeat_login_keys_by_subject_and_preserves_id_role_and_owned_data(session):
    existing = User(
        email="old@example.com",
        display_name="Old Name",
        google_sub="stable-subject",
        role=UserRole.ADMIN,
    )
    session.add(existing)
    session.commit()
    session.refresh(existing)
    original_id = existing.id

    updated = upsert_google_user(
        GoogleIdentity("stable-subject", "new@example.com", "New Name"),
        session,
        now=NOW,
    )

    assert updated.id == original_id
    assert updated.role == "admin"
    assert updated.email == "new@example.com"
    assert updated.display_name == "New Name"
    assert session.exec(select(User)).all() == [updated]


def test_application_credential_round_trip_is_signed_and_expiring():
    token = issue_app_credential(42, signing_secret=SECRET, ttl_seconds=300, now=NOW)

    claims = verify_app_credential(token, signing_secret=SECRET, now=NOW + timedelta(seconds=299))

    assert claims.user_id == 42
    assert claims.expires_at == NOW + timedelta(seconds=300)
    assert "42" not in token


@pytest.mark.parametrize("failure", ["expired", "altered", "wrong-secret"])
def test_invalid_application_credentials_are_rejected_without_secret_leakage(failure):
    token = issue_app_credential(7, signing_secret=SECRET, ttl_seconds=60, now=NOW)
    candidate = f"{token}x" if failure == "altered" else token
    secret = "different-secret" if failure == "wrong-secret" else SECRET
    checked_at = NOW + timedelta(seconds=61) if failure == "expired" else NOW

    with pytest.raises(AuthError) as caught:
        verify_app_credential(candidate, signing_secret=secret, now=checked_at)

    assert candidate not in str(caught.value)
    assert SECRET not in str(caught.value)


def test_desktop_completion_code_is_single_use_and_server_bound():
    current = [NOW]
    codes = CompletionCodeStore(ttl_seconds=60, clock=lambda: current[0])
    code = codes.issue(user_id=9, server_origin="https://one.example")

    assert codes.exchange(code, server_origin="https://one.example") == 9
    with pytest.raises(AuthError):
        codes.exchange(code, server_origin="https://one.example")


def test_desktop_completion_code_rejects_wrong_server_and_expiry():
    current = [NOW]
    codes = CompletionCodeStore(ttl_seconds=60, clock=lambda: current[0])
    wrong_server_code = codes.issue(user_id=9, server_origin="https://one.example")
    with pytest.raises(AuthError):
        codes.exchange(wrong_server_code, server_origin="https://two.example")

    expired_code = codes.issue(user_id=9, server_origin="https://one.example")
    current[0] += timedelta(seconds=61)
    with pytest.raises(AuthError):
        codes.exchange(expired_code, server_origin="https://one.example")
@pytest.mark.parametrize("kind", ["state", "completion"])
def test_one_use_stores_are_atomic_under_concurrent_consumption(kind):
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial
    from threading import Barrier

    from sra.auth.google_oauth import AuthError, CompletionCodeStore, LoginAttempt, OAuthStateStore

    now = datetime.now(UTC)
    barrier = Barrier(16)
    if kind == "state":
        store = OAuthStateStore(signing_secret="test-concurrent-state-secret", ttl_seconds=60, clock=lambda: now)
        token = store.issue(LoginAttempt(client="web", server_origin="https://study.example"))
        consume = partial(store.consume, token)
    else:
        codes = CompletionCodeStore(ttl_seconds=60, clock=lambda: now)
        token = codes.issue(user_id=1, server_origin="https://study.example")
        consume = partial(codes.exchange, token, server_origin="https://study.example")

    def attempt(_):
        barrier.wait(timeout=5)
        try:
            consume()
            return True
        except AuthError:
            return False

    with ThreadPoolExecutor(max_workers=16) as workers:
        assert sum(workers.map(attempt, range(16))) == 1
