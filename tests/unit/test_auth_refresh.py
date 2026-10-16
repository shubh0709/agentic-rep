"""SRA-306 refresh-credential primitives: store, issue, verify."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Barrier

import pytest

from sra.auth.google_oauth import (
    AuthError,
    RefreshTokenStore,
    issue_app_credential,
    issue_refresh_credential,
    verify_app_credential,
    verify_refresh_credential,
)

pytestmark = pytest.mark.phase3

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
SECRET = "test-signing-secret-with-enough-entropy"
OTHER_SECRET = "different-signing-secret-with-entropy"
ORIGIN = "https://study.example"


def test_refresh_store_issue_and_consume_returns_bound_user_and_origin():
    current = [NOW]
    store = RefreshTokenStore(ttl_seconds=600, clock=lambda: current[0])

    jti = store.issue(user_id=42, server_origin=ORIGIN)

    assert store.consume(jti, server_origin=ORIGIN) == (42, ORIGIN)


def test_refresh_store_consume_is_single_use():
    current = [NOW]
    store = RefreshTokenStore(ttl_seconds=600, clock=lambda: current[0])
    jti = store.issue(user_id=42, server_origin=ORIGIN)

    store.consume(jti, server_origin=ORIGIN)

    with pytest.raises(AuthError):
        store.consume(jti, server_origin=ORIGIN)


def test_refresh_store_consume_rejects_wrong_server_origin():
    current = [NOW]
    store = RefreshTokenStore(ttl_seconds=600, clock=lambda: current[0])
    jti = store.issue(user_id=42, server_origin="https://one.example")

    with pytest.raises(AuthError):
        store.consume(jti, server_origin="https://two.example")


def test_refresh_store_consume_rejects_expired_jti():
    current = [NOW]
    store = RefreshTokenStore(ttl_seconds=60, clock=lambda: current[0])
    jti = store.issue(user_id=42, server_origin=ORIGIN)
    current[0] = NOW + timedelta(seconds=61)

    with pytest.raises(AuthError):
        store.consume(jti, server_origin=ORIGIN)


def test_refresh_store_revoke_family_removes_every_active_jti_for_user():
    current = [NOW]
    store = RefreshTokenStore(ttl_seconds=600, clock=lambda: current[0])
    first = store.issue(user_id=1, server_origin=ORIGIN)
    second = store.issue(user_id=1, server_origin=ORIGIN)
    other = store.issue(user_id=2, server_origin=ORIGIN)

    store.revoke_family(user_id=1)

    with pytest.raises(AuthError):
        store.consume(first, server_origin=ORIGIN)
    with pytest.raises(AuthError):
        store.consume(second, server_origin=ORIGIN)
    assert store.consume(other, server_origin=ORIGIN) == (2, ORIGIN)


def test_refresh_store_concurrent_consume_is_single_winner():
    current = [NOW]
    store = RefreshTokenStore(ttl_seconds=600, clock=lambda: current[0])
    jti = store.issue(user_id=1, server_origin=ORIGIN)
    barrier = Barrier(16)
    consume = partial(store.consume, jti, server_origin=ORIGIN)

    def runner(_):
        barrier.wait(timeout=5)
        try:
            consume()
            return True
        except AuthError:
            return False

    with ThreadPoolExecutor(max_workers=16) as workers:
        assert sum(workers.map(runner, range(16))) == 1


def test_refresh_credential_round_trip_yields_matching_claims():
    jti = "fixed-jti-value-for-round-trip"
    credential = issue_refresh_credential(
        42, jti, signing_secret=SECRET, ttl_seconds=300, now=NOW,
    )

    claims = verify_refresh_credential(
        credential, signing_secret=SECRET, now=NOW + timedelta(seconds=299),
    )

    assert claims.user_id == 42
    assert claims.jti == jti
    assert claims.expires_at == NOW + timedelta(seconds=300)
    assert jti not in credential


def test_refresh_verifier_rejects_access_credential_payload_without_leakage():
    access = issue_app_credential(7, signing_secret=SECRET, ttl_seconds=60, now=NOW)

    with pytest.raises(AuthError) as caught:
        verify_refresh_credential(access, signing_secret=SECRET, now=NOW)

    assert access not in str(caught.value)
    assert SECRET not in str(caught.value)


def test_refresh_verifier_rejects_wrong_signing_secret_without_leakage():
    refresh = issue_refresh_credential(
        7, "jti-x", signing_secret=SECRET, ttl_seconds=60, now=NOW,
    )

    with pytest.raises(AuthError) as caught:
        verify_refresh_credential(refresh, signing_secret=OTHER_SECRET, now=NOW)

    assert refresh not in str(caught.value)
    assert SECRET not in str(caught.value)
    assert OTHER_SECRET not in str(caught.value)


def test_refresh_verifier_rejects_expired_credential():
    credential = issue_refresh_credential(
        7, "jti-x", signing_secret=SECRET, ttl_seconds=60, now=NOW,
    )

    with pytest.raises(AuthError):
        verify_refresh_credential(
            credential, signing_secret=SECRET, now=NOW + timedelta(seconds=61),
        )


def test_access_verifier_rejects_refresh_credential_payload():
    refresh = issue_refresh_credential(
        7, "jti-x", signing_secret=SECRET, ttl_seconds=60, now=NOW,
    )

    with pytest.raises(AuthError) as caught:
        verify_app_credential(refresh, signing_secret=SECRET, now=NOW)

    assert refresh not in str(caught.value)
    assert SECRET not in str(caught.value)
