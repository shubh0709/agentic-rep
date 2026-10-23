# SRA-304 — Per-user and per-IP API rate limiting

**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting · **Status:** verified

## Summary

Implement a configurable in-process token-bucket limiter keyed by authenticated user ID for protected traffic and trusted client IP for anonymous authentication traffic. Authentication resolves the trusted principal before limiting, while the limiter runs before model inference or database mutation. Exhausted buckets return the standard `429` response with `Retry-After`; successful requests do not. Administrators can inspect safe policy and aggregate state without receiving study content or credentials. Each process owns independent buckets that expire when idle and clear on shutdown or app recreation.

## Unlocks

- A self-hosted server can bound account- and IP-level request cost without coupling the limiter to model providers or leaking user data.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/api/rate_limit.py`](../src/sra/api/rate_limit.py) | edit | Implements synchronized buckets, key policy, monotonic refill, cleanup, and typed inspection data. |
| [`src/sra/config/`](../src/sra/config/), [`.env.example`](../.env.example) | edit | Validate positive authenticated/anonymous rates and capacities with explicit units. |
| [`src/sra/auth/dependencies.py`](../src/sra/auth/dependencies.py) | edit | Supplies the trusted `User.id` without parsing credentials twice or accepting caller-owned identity. |
| [`src/sra/api/routers/__init__.py`](../src/sra/api/routers/__init__.py) | edit | Applies anonymous, authenticated, and admin policies in the correct dependency order. |
| [`src/sra/api/routers/admin.py`](../src/sra/api/routers/admin.py) | edit | Exposes `/admin/rate-limits` with safe aggregates only. |
| [`src/sra/api/app.py`](../src/sra/api/app.py) | edit | Gives every app factory isolated limiter state with explicit shutdown/reset. |
| [`tests/unit/test_rate_limit.py`](../tests/unit/test_rate_limit.py) | edit | Proves refill math, independence, concurrency, cleanup, settings, and `Retry-After` with a fake clock. |
| [`tests/integration/test_rate_limited_routes.py`](../tests/integration/test_rate_limited_routes.py) | edit | Proves pre-side-effect `429` behavior through real routes. |
| [`README.md`](../README.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md) | edit | Document units, proxy trust, one-process limits, reset behavior, and the future Redis seam. |

## Acceptance criteria

- [x] Configured authenticated and anonymous buckets allow the documented burst, limit the next request, and refill correctly without sleeps
- [x] Two users and two anonymous IPs have independent state; no request key can be selected from caller-supplied user data or untrusted forwarding headers
- [x] A limited request returns the standard `429` body and accurate `Retry-After` before model calls or mutations occur
- [x] Concurrent requests cannot overspend a bucket, and inactive-key cleanup bounds memory growth
- [x] Health remains available; protected and anonymous route coverage matches the documented policy
- [x] Admins can inspect safe policy/state aggregates, ordinary users receive `403`, and no study content, token, or raw credential is exposed
- [x] Single-process, restart, reverse-proxy, and future distributed-store limitations are documented honestly

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_rate_limit.py -v
pytest tests/integration/test_rate_limited_routes.py -v
pytest tests/integration/test_authenticated_routes.py -v
pytest -q
ruff check .
pyright
```

Use a barrier-driven concurrency test, not timing luck. Count downstream model/repository calls to prove a limited request caused none.

## Manual verification

- [ ] On a disposable server, exhaust user A's bucket; record the safe body, `Retry-After`, and that no entry or model call occurred; confirm user B still succeeds.
- [ ] Exhaust the anonymous login bucket, then confirm the reported refill or documented app reset recovers it; verify `/api/health` stays `200`.
- [ ] Confirm an admin receives safe `/api/v1/admin/rate-limits` data and an ordinary user receives `403`; never use these limits on a shared production instance.

**Verification:**
- **2026-10-14, macOS development environment:** one Uvicorn worker, temporary SQLite, real routes/auth/TCP, controlled monotonic clock, substituted extraction model; capacities `2`, refill `0.5` tokens/second, TTL `3600` seconds.
- Authenticated sequence: A ingested twice (`201`, IDs **1 and 2**) then received `429`; B independently received `200`. At denial: exactly **2 entries, 12 schedules, 2 inference calls**, body `{"detail":"Rate limit exceeded"}`, `Retry-After: 2`.
- Anonymous/proxy policy returned `302, 302, 429` despite spoofed `X-Forwarded-For` headers; ASGI tests covered two peer addresses and all four anonymous auth endpoints sharing one IP bucket.
- Advancing the clock two seconds found IDs **1 and 2**; two more seconds allowed ID **3**. Health stayed `200`; shutdown/app recreation cleared buckets. This proves app reset, not an OS process restart.
- Admin access returned `200` and ordinary access `403`; responses exposed only `authenticated`/`anonymous`, each with `capacity`, `refill_tokens_per_second`, and `active_bucket_count`.
- Focused tests reported **34 passed**; full `pytest -q` reported **363 passed, 10 skipped**; `ruff check .` and `pyright` passed. No live Google, Keychain, physical desktop, reverse proxy, external model, or multiple-worker enforcement was tested.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
