# SRA-304 — Per-user and per-IP API rate limiting

**Type:** Task
**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting
**Priority:** High — authenticated model-backed routes need enforceable cost and abuse bounds before multi-user exposure
**Status:** Implemented and verified — 2026-10-14; real loopback HTTP with substituted inference
**Blocked by:** SRA-302
**Blocks:** SRA-305

## Summary

Implement a configurable in-process token-bucket limiter, keyed by authenticated
user ID for protected traffic and client IP for anonymous auth traffic. Wire it
to the API, return actionable `429` responses, and expose safe limiter
configuration/state summaries to admins without revealing study content or raw
credentials.

Before this ticket, one account or IP can send unlimited model-backed requests.
After it, bursts within the configured capacity succeed, exhausted buckets
receive `429` plus `Retry-After`, different principals remain independent, and
tokens refill according to a controlled monotonic clock.

## Where things stand right now

`api/rate_limit.py` now implements synchronized token buckets, and `Settings`
validates both policies at app construction. SRA-302 supplies the trusted user
and `require_admin` boundary. Each app factory creates its own `RateLimitState`;
lifespan shutdown or explicit `reset()` clears it. Consumption and summary calls
expire idle keys. TTL must cover a full refill so expiry cannot bypass the policy.

The plan intentionally chooses an in-process limiter for one self-hosted server.
That state does not coordinate across multiple workers/instances and does not
survive restart. This limitation must remain visible; a passing single-process
test is not evidence of distributed enforcement.

## What this ticket unlocks

With a test threshold of two requests per minute, user A can make two protected
requests and gets `429` on the third, while user B can still make requests. An
anonymous client exhausting login attempts does not consume either user's
authenticated bucket. Advancing the controlled clock refills capacity, and the
`Retry-After` value tells the caller when retrying can succeed.

## How this fits into the whole app

```mermaid
flowchart TD
    Request[Incoming API request] --> Kind{Authenticated route?}
    Kind -->|Yes| Auth[require_user resolves trusted User]
    Auth --> UserKey[Key user:user.id]
    Kind -->|Anonymous auth route| IP[Resolve trusted client IP policy]
    IP --> IPKey[Key ip:address]
    UserKey --> Bucket[Token bucket owned by app process]
    IPKey --> Bucket
    Bucket -->|Token available| Route[Run route and downstream work]
    Bucket -->|Empty| Limited[429 and Retry-After]
    Admin[Authenticated admin] --> Summary[/admin/rate-limits summary]
    Summary --> Bucket
```

Runtime ordering matters: authenticated requests must resolve the credential
before selecting `user:<id>`, but the limiter must run before model inference or
database mutation. Anonymous login/callback traffic uses a separately configured
IP bucket. The health check should remain cheap and exempt so operators can
diagnose a limited or unhealthy instance.

Use monotonic elapsed time for refill math so wall-clock adjustments cannot add
or remove tokens. Guard shared state because synchronous FastAPI handlers may run
concurrently in worker threads. Bucket cleanup must prevent inactive keys from
growing memory forever.

## Files and why they matter

| File | Role and required change |
|---|---|
| [`src/sra/api/rate_limit.py`](../src/sra/api/) | New limiter, key policy, dependency adapters, clock boundary, cleanup, and typed inspection summary. Separate token math from FastAPI error translation. |
| [`src/sra/config.py`](../src/sra/config.py), [`.env.example`](../.env.example) | Add positive authenticated/anonymous rates and burst capacities with clear units. Document cache clearing in tests. Demo-mode overrides remain Phase 6. |
| [`src/sra/auth/dependencies.py`](../src/sra/auth/dependencies.py) | Make the trusted user available to the authenticated limiter without parsing the credential again or accepting a caller-supplied ID. |
| [`src/sra/api/routers/__init__.py`](../src/sra/api/routers/__init__.py), protected routers, and auth router | Apply the correct dependency in the correct order. Do not accidentally limit only one route or run costly work first. |
| [`src/sra/api/routers/admin.py`](../src/sra/api/routers/) | Add `/admin/rate-limits` with configuration and safe aggregate/bucket statistics. Avoid bearer tokens, full IPs where unnecessary, or study content. |
| [`src/sra/api/app.py`](../src/sra/api/app.py) | Own one limiter per app process/lifespan so tests and app factories receive isolated state and shutdown/cleanup is explicit. |
| [`tests/unit/test_rate_limit.py`](../tests/unit/) | Prove refill math, independence, concurrency, cleanup, settings, and `Retry-After` using a fake monotonic clock. |
| [`tests/integration/test_rate_limited_routes.py`](../tests/) | Prove dependency ordering and `429` behavior through real authenticated/anonymous routes without calling live providers or models. |
| [`README.md`](../README.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md) | Document units, tuning, reverse-proxy/IP assumptions, one-process limitations, restart behavior, and future Redis seam. |

## TDD starting point

Prepare a typed token-bucket class or functions and FastAPI dependency stubs with
teaching docstrings. Supply a fake monotonic clock and deterministic tests; never
use `sleep`. Tests should specify whether a rate is a refill rate, a fixed-window
count, or a burst capacity—do not use an ambiguous single "per minute" number in
code while asserting token-bucket behavior.

The route tests should authenticate through SRA-302's real dependency and
replace only Google/model boundaries. A direct unit test proves math; an HTTP
test proves the limiter actually protects the route before its side effect.

## What to implement

- Add validated settings for authenticated and anonymous refill rates/capacities.
  State units and defaults; reject zero/negative or internally inconsistent
  values at startup.
- Implement token-bucket refill/consume logic with a monotonic clock, bounded
  capacity, deterministic `Retry-After`, and synchronization around mutations.
- Key authenticated traffic from the server-resolved `User.id`. Key anonymous
  auth traffic from the effective client IP under a documented proxy-trust
  policy; never trust arbitrary `X-Forwarded-For` by default.
- Apply limiting after authentication/key resolution but before route work. Do
  not consume an anonymous and authenticated bucket for the same successful
  protected request.
- Return the standard error shape with HTTP `429` and a rounded-up,
  non-negative `Retry-After` header. Successful requests should not receive that
  header.
- Keep `/api/health` exempt. Explicitly list which auth/callback/static routes
  use anonymous limits and which API routes use authenticated limits.
- Bound or expire inactive bucket entries. Expose/reset state through explicit
  app/test ownership rather than mutating private globals.
- Add an admin-only rate-limit summary showing configured policy and safe
  operational counts. Do not expose tokens, secrets, study content, or an easy
  directory of raw client IP addresses.
- Document that each process has independent buckets and restart clears them.
  Identify a Redis-backed store as the replacement seam for multi-worker/demo
  scaling; do not claim it is implemented.

## Where to look

- Plan requirement R22 and KTD11.
- Technical design §3.13 and §4.
- SRA-302 for trusted identity and admin dependencies.
- FastAPI app factory/tests for state ownership and dependency overrides.

## Acceptance criteria

- [x] Configured authenticated and anonymous buckets allow the documented burst, limit the next request, and refill correctly without sleeps
- [x] Two users and two anonymous IPs have independent state; no request key can be selected from caller-supplied user data or untrusted forwarding headers
- [x] A limited request returns the standard `429` body and accurate `Retry-After` before model calls or mutations occur
- [x] Concurrent requests cannot overspend a bucket, and inactive-key cleanup bounds memory growth
- [x] Health remains available; protected and anonymous route coverage matches the documented policy
- [x] Admins can inspect safe policy/state aggregates, ordinary users receive `403`, and no study content, token, or raw credential is exposed
- [x] Single-process, restart, reverse-proxy, and future distributed-store limitations are documented honestly

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_rate_limit.py -v
pytest tests/integration/test_rate_limited_routes.py -v
pytest tests/integration/test_authenticated_routes.py -v
pytest -q
ruff check .
pyright
```

Include a barrier-driven concurrency test rather than relying on timing luck.
The integration test should count downstream model/repository invocations and
show the limited request caused none.

## Manual verification

Start a disposable server with low, explicit limits and a disposable database.
Authenticate user A and issue requests until the server returns `429`; record
status, safe response body, `Retry-After`, and confirmation that no additional
entry was created. Sign in as user B and confirm an independent request succeeds.
Exercise the anonymous login limit from localhost, then wait the reported period
or restart the disposable server and verify the documented refill/reset behavior.

Use an admin account to inspect `/api/v1/admin/rate-limits`, then confirm a user
account receives `403`. Restore normal thresholds and verify a successful ingest
after the limited state clears. Never perform this low-limit test against a
shared production instance.

## Verification record

- **2026-10-14, local macOS development environment:** one disposable Uvicorn
  worker on an ephemeral loopback port, file-backed temporary SQLite database,
  locally issued signed application credentials for A/B/admin, both policies
  capacity `2`, refill `0.5` tokens/second, TTL `3600` seconds. Used a controlled
  monotonic clock and a substituted extraction model; real routes, authentication,
  graph, repository, schedules, SQL storage, and TCP HTTP remained active.
- **Authenticated sequence:** A ingested twice (`201`, IDs **1 and 2**), then
  received `429`; B's search independently returned `200`. At denial there were
  exactly **2 entries, 12 schedules, and 2 inference calls**. The body was
  `{"detail":"Rate limit exceeded"}` and `Retry-After: 2`.
- **Anonymous/proxy policy:** localhost login returned `302, 302, 429` despite
  different spoofed `X-Forwarded-For` headers, with Uvicorn proxy headers disabled.
  Automated ASGI tests additionally verify two independent peer addresses and
  all four anonymous auth endpoints sharing a single IP bucket.
- **Recovery and lifecycle:** advancing the clock by two seconds allowed search
  to find exactly IDs **1 and 2**; two more seconds allowed ingestion of ID **3**.
  Health remained `200`. Shutdown cleared the buckets; a newly constructed app
  had full capacity. The temporary server and database were cleaned up. This
  establishes app recreation/reset, not an operating-system process restart.
- **Admin/redaction:** ordinary user access returned `403` even while the user's
  bucket was depleted; admin access returned `200`. The only response fields
  were `authenticated`/`anonymous`, each containing `capacity`,
  `refill_tokens_per_second`, and `active_bucket_count` (counts 3/1 during smoke).
- **Automated checks:**
  `pytest tests/unit/test_rate_limit.py tests/integration/test_rate_limited_routes.py tests/integration/test_authenticated_routes.py -q`
  **34 passed**; full `pytest -q` **363 passed, 10 skipped**; `ruff check .` and
  `pyright` passed. The barrier-driven 16-thread test permits exactly four
  requests with capacity four. Settings, rounded waits, cleanup/reset, role checks,
  provider-call ordering, and isolated app factories are covered.
- **Limits:** no live Google, Keychain, physical desktop, external model call,
  reverse-proxy deployment, or multiple-worker enforcement was tested here.
  Default-suite skips retain their existing opt-in/platform requirements.
  The smoke bind initially hit the sandbox restriction, then succeeded after
  automatic approval allowed the same disposable localhost-only check.
  Real-provider/desktop phase acceptance remains in SRA-305.

Policy defaults, exact route coverage, proxy trust, tuning, and the future Redis
seam are recorded in [README.md](../README.md#api-rate-limits). Tests clear cached
settings before constructing fresh apps, as documented in CONTRIBUTING.md.

## Definition of Done

- [x] Acceptance criteria are met with observed HTTP evidence recorded
- [x] Focused, full-suite, lint, and type checks pass
- [x] Configuration, topology limitations, and operational recovery are documented
- [x] Ticket board and progress documentation reflect the verified state
