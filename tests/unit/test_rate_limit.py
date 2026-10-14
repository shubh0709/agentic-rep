"""SRA-304 deterministic and concurrent token-bucket contracts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from sra.api.rate_limit import TokenBucketLimiter

pytestmark = pytest.mark.phase3


class FakeMonotonicClock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def make_limiter(clock, *, capacity=2, refill=0.5, idle_ttl=60):
    return TokenBucketLimiter(
        capacity=capacity,
        refill_tokens_per_second=refill,
        clock=clock,
        idle_ttl_seconds=idle_ttl,
    )


@pytest.mark.parametrize(
    ("capacity", "refill", "idle_ttl"),
    [(0, 1, 60), (-1, 1, 60), (1, 0, 60), (1, -1, 60), (1, 1, 0),
     (2, 0.01, 60), (1, float("nan"), 60), (1, float("inf"), 60), (1, 1, float("inf"))],
)
def test_invalid_policy_is_rejected(capacity, refill, idle_ttl):
    with pytest.raises(ValueError):
        TokenBucketLimiter(
            capacity=capacity,
            refill_tokens_per_second=refill,
            clock=lambda: 0,
            idle_ttl_seconds=idle_ttl,
        )


def test_bucket_allows_burst_then_returns_rounded_up_retry_after():
    clock = FakeMonotonicClock()
    limiter = make_limiter(clock, capacity=2, refill=0.5)

    assert limiter.consume("user:1").allowed
    assert limiter.consume("user:1").allowed
    limited = limiter.consume("user:1")

    assert not limited.allowed
    assert limited.retry_after_seconds == 2


def test_fractional_refill_is_bounded_by_capacity_without_sleeping():
    clock = FakeMonotonicClock()
    limiter = make_limiter(clock, capacity=2, refill=0.5)
    assert limiter.consume("user:1").allowed
    assert limiter.consume("user:1").allowed

    clock.advance(1.9)
    assert not limiter.consume("user:1").allowed
    clock.advance(0.1)
    assert limiter.consume("user:1").allowed

    clock.advance(100)
    assert limiter.consume("user:1").allowed
    assert limiter.consume("user:1").allowed
    assert not limiter.consume("user:1").allowed


def test_principal_keys_have_independent_buckets():
    limiter = make_limiter(FakeMonotonicClock(), capacity=1, refill=1)

    assert limiter.consume("user:1").allowed
    assert not limiter.consume("user:1").allowed
    assert limiter.consume("user:2").allowed
    assert limiter.consume("ip:192.0.2.1").allowed


def test_concurrent_requests_cannot_overspend_one_bucket():
    capacity = 4
    worker_count = 16
    limiter = make_limiter(FakeMonotonicClock(), capacity=capacity, refill=1)
    barrier = Barrier(worker_count)

    def consume_together(_):
        barrier.wait()
        return limiter.consume("user:1").allowed

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(consume_together, range(worker_count)))

    assert sum(results) == capacity


def test_cleanup_removes_only_expired_idle_buckets():
    clock = FakeMonotonicClock()
    limiter = make_limiter(clock, capacity=1, refill=1, idle_ttl=60)
    limiter.consume("old")
    clock.advance(30)
    limiter.consume("recent")
    clock.advance(31)

    assert limiter.cleanup() == 1
    summary = limiter.summary()
    assert summary.active_bucket_count == 1


def test_summary_exposes_policy_and_counts_but_not_principal_keys():
    limiter = make_limiter(FakeMonotonicClock(), capacity=3, refill=0.25)
    limiter.consume("user:secret-subject")

    summary = limiter.summary()

    assert summary.capacity == 3
    assert summary.refill_tokens_per_second == 0.25
    assert summary.active_bucket_count == 1
    assert "secret-subject" not in repr(summary)


def test_request_cleanup_and_reset_are_explicit_without_refilling_active_keys():
    clock = FakeMonotonicClock()
    limiter = make_limiter(clock, capacity=1, refill=1, idle_ttl=60)
    limiter.consume("old")
    clock.advance(61)
    limiter.consume("current")
    assert limiter.summary().active_bucket_count == 1
    assert not limiter.consume("current").allowed
    limiter.reset()
    assert limiter.summary().active_bucket_count == 0
    assert limiter.consume("current").allowed


@pytest.mark.parametrize("field,value", [
    ("rate_limit_authenticated_capacity", 0),
    ("rate_limit_anonymous_capacity", -1),
    ("rate_limit_authenticated_refill_per_second", 0),
    ("rate_limit_anonymous_refill_per_second", float("nan")),
    ("rate_limit_idle_ttl_seconds", 1),
])
def test_settings_reject_invalid_or_inconsistent_policy(field, value):
    from sra.config import Settings

    with pytest.raises(ValueError):
        Settings.model_validate({"openai_api_key": "test", field: value})
