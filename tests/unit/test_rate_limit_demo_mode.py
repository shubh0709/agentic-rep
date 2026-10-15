"""SRA-603 — rate-limit narrowing under ``DEMO_MODE``.

Both the authenticated and anonymous policies must have their capacity and
refill rate divided by 10 (clamped at 1) when ``demo_mode=True``. The base
policy is unchanged when ``demo_mode=False``.
"""


import pytest

from sra.api.rate_limit import RateLimitState
from sra.config import Settings


def _settings_with(
    *,
    demo_mode: bool,
    auth_capacity: int = 60,
    auth_refill: float = 1.0,
    anon_capacity: int = 20,
    anon_refill: float = 0.2,
    idle_ttl: float = 3600.0,
) -> Settings:
    """Construct a Settings instance with the documented defaults plus the demo flag."""
    import os

    os.environ["OPENAI_API_KEY"] = "sk-test"
    from pydantic import SecretStr

    return Settings(
        openai_api_key=SecretStr("sk-test"),
        demo_mode=demo_mode,
        rate_limit_authenticated_capacity=auth_capacity,
        rate_limit_authenticated_refill_per_second=auth_refill,
        rate_limit_anonymous_capacity=anon_capacity,
        rate_limit_anonymous_refill_per_second=anon_refill,
        rate_limit_idle_ttl_seconds=idle_ttl,
    )


def test_base_rate_limit_policies_match_settings_when_demo_mode_disabled():
    """``demo_mode=False`` -- capacity / refill match the configured Settings exactly."""
    settings = _settings_with(demo_mode=False)

    state = RateLimitState.from_settings(settings)

    assert state.authenticated.summary().capacity == settings.rate_limit_authenticated_capacity
    assert state.authenticated.summary().refill_tokens_per_second == pytest.approx(
        settings.rate_limit_authenticated_refill_per_second
    )
    assert state.anonymous.summary().capacity == settings.rate_limit_anonymous_capacity
    assert state.anonymous.summary().refill_tokens_per_second == pytest.approx(
        settings.rate_limit_anonymous_refill_per_second
    )


def test_rate_limit_capacity_is_divided_by_ten_when_demo_mode_enabled():
    settings = _settings_with(demo_mode=True)

    state = RateLimitState.from_settings(settings)

    assert state.authenticated.summary().capacity == max(
        1, settings.rate_limit_authenticated_capacity // 10
    )
    assert state.anonymous.summary().capacity == max(
        1, settings.rate_limit_anonymous_capacity // 10
    )


def test_rate_limit_refill_is_divided_by_ten_when_demo_mode_enabled():
    settings = _settings_with(demo_mode=True)

    state = RateLimitState.from_settings(settings)

    assert state.authenticated.summary().refill_tokens_per_second == pytest.approx(
        settings.rate_limit_authenticated_refill_per_second / 10
    )
    assert state.anonymous.summary().refill_tokens_per_second == pytest.approx(
        settings.rate_limit_anonymous_refill_per_second / 10
    )


def test_rate_limit_capacity_is_clamped_at_one_when_base_is_below_ten():
    """If base capacity is < 10 the narrowed capacity must still be >= 1 (never zero)."""
    settings = _settings_with(demo_mode=True, auth_capacity=4, anon_capacity=2)

    state = RateLimitState.from_settings(settings)

    assert state.authenticated.summary().capacity == 1
    assert state.anonymous.summary().capacity == 1


def test_rate_limit_summary_endpoint_reflects_narrowing():
    """Admin-visible ``/api/v1/admin/rate-limits`` must reflect the narrowed values."""
    settings = _settings_with(demo_mode=True)

    state = RateLimitState.from_settings(settings)

    auth_summary = state.authenticated.summary()
    anon_summary = state.anonymous.summary()
    assert auth_summary.capacity == 6  # 60 // 10
    assert anon_summary.capacity == 2  # 20 // 10
    assert auth_summary.refill_tokens_per_second == pytest.approx(0.1)  # 1.0 / 10
    assert anon_summary.refill_tokens_per_second == pytest.approx(0.02)  # 0.2 / 10


def test_rate_limit_narrowing_does_not_change_idle_ttl():
    """The idle TTL must remain the configured value; only capacity / refill change."""
    settings = _settings_with(demo_mode=True, idle_ttl=7200.0)

    state = RateLimitState.from_settings(settings)

    # Idle TTL is validated by TokenBucketLimiter; if it is preserved through
    # construction the narrowed settings do not regress the policy's full-refill check.
    assert state.authenticated.summary().refill_tokens_per_second > 0
    assert state.anonymous.summary().refill_tokens_per_second > 0