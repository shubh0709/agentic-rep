"""Deterministic in-process token-bucket contracts for SRA-304."""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil, isfinite
from threading import Lock
from time import monotonic

from fastapi import Depends, HTTPException, Request

from sra.auth.dependencies import require_user
from sra.config import Settings
from sra.db.models import User


@dataclass(frozen=True)
class LimitDecision:
    """Result of one atomic token consumption attempt."""

    allowed: bool
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class RateLimitSummary:
    """Safe operational state; principal keys and token values stay private."""

    capacity: int
    refill_tokens_per_second: float
    active_bucket_count: int


class TokenBucketLimiter:
    """Thread-safe token buckets keyed by a trusted server-side principal."""

    def __init__(
        self,
        *,
        capacity: int,
        refill_tokens_per_second: float,
        clock: Callable[[], float],
        idle_ttl_seconds: float = 3600,
    ) -> None:
        """Validate policy and create isolated, empty limiter state."""
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if not isfinite(refill_tokens_per_second) or refill_tokens_per_second <= 0:
            raise ValueError("refill rate must be finite and positive")
        if not isfinite(idle_ttl_seconds) or idle_ttl_seconds < capacity / refill_tokens_per_second:
            raise ValueError("idle TTL must be finite and at least the full refill time")
        self._capacity = capacity
        self._refill = refill_tokens_per_second
        self._clock = clock
        self._idle_ttl = idle_ttl_seconds
        self._lock = Lock()
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def consume(self, key: str) -> LimitDecision:
        """Atomically consume one token or return a deterministic wait time."""
        with self._lock:
            now = self._clock()
            self._cleanup(now)
            tokens, updated = self._buckets.pop(key, (float(self._capacity), now))
            tokens = min(self._capacity, tokens + max(0, now - updated) * self._refill)
            allowed = tokens >= 1
            if allowed:
                tokens -= 1
            self._buckets[key] = (tokens, now)
            return LimitDecision(allowed, None if allowed else max(1, ceil((1 - tokens) / self._refill)))

    def _cleanup(self, now: float) -> int:
        # Ordered by last access, so cleanup visits only expired entries.
        removed = 0
        while self._buckets:
            _, (_, updated) = next(iter(self._buckets.items()))
            if now - updated < self._idle_ttl:
                break
            self._buckets.popitem(last=False)
            removed += 1
        return removed

    def cleanup(self) -> int:
        """Remove idle keys and return the number removed."""
        with self._lock:
            return self._cleanup(self._clock())

    def reset(self) -> None:
        """Explicitly discard process-local state on shutdown or in tests."""
        with self._lock:
            self._buckets.clear()

    def summary(self) -> RateLimitSummary:
        """Return policy and aggregate counts without disclosing bucket keys."""
        with self._lock:
            self._cleanup(self._clock())
            return RateLimitSummary(self._capacity, self._refill, len(self._buckets))


@dataclass(frozen=True)
class RateLimitState:
    """One pair of stores per application, never shared across factories."""

    authenticated: TokenBucketLimiter
    anonymous: TokenBucketLimiter

    @classmethod
    def from_settings(cls, settings: Settings, clock: Callable[[], float] = monotonic) -> "RateLimitState":
        return cls(
            authenticated=TokenBucketLimiter(
                capacity=settings.rate_limit_authenticated_capacity,
                refill_tokens_per_second=settings.rate_limit_authenticated_refill_per_second,
                idle_ttl_seconds=settings.rate_limit_idle_ttl_seconds,
                clock=clock,
            ),
            anonymous=TokenBucketLimiter(
                capacity=settings.rate_limit_anonymous_capacity,
                refill_tokens_per_second=settings.rate_limit_anonymous_refill_per_second,
                idle_ttl_seconds=settings.rate_limit_idle_ttl_seconds,
                clock=clock,
            ),
        )

    def reset(self) -> None:
        self.authenticated.reset()
        self.anonymous.reset()


def get_rate_limits(request: Request) -> RateLimitState:
    return request.app.state.rate_limits


def _enforce(decision: LimitDecision) -> None:
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def limit_authenticated(
    user: User = Depends(require_user),
    limits: RateLimitState = Depends(get_rate_limits),
) -> None:
    """Reuse FastAPI's cached trusted identity; never accept a caller's ID."""
    assert user.id is not None
    _enforce(limits.authenticated.consume(f"user:{user.id}"))


def limit_anonymous(request: Request, limits: RateLimitState = Depends(get_rate_limits)) -> None:
    """Use the ASGI peer only; forwarding-header trust belongs to the server."""
    address = request.client.host if request.client else "unknown"
    _enforce(limits.anonymous.consume(f"ip:{address}"))
