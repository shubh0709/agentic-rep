# SRA-305 — Phase 3 integration verification

**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting · **Status:** implemented

## Summary

Verify Phase 3 across migration, Google sign-in, web sessions, desktop loopback login, Keychain reuse, authenticated voice capture, tenant isolation, RBAC, and rate limiting. Two real accounts and the native macOS client are required because isolated tests cannot establish those boundaries. SRA-301–SRA-304 are implemented; verification repaired callback, attempt-binding, lifecycle, redaction, and authenticated-audio defects. Offline and cached-Whisper automation passes, but real Google, browser-cookie, Keychain, microphone, and hotkey acceptance remain, so Phase 3 stays blocked.

## Unlocks

- Completion provides the evidence required to close Phase 3 and start Phase 4 with proof that one shared server preserves user data and bounded access.

## Files

| File | Change | Why |
|---|---|---|
| [`tests/integration/test_phase3_migration.py`](../tests/integration/test_phase3_migration.py) | edit | Rehearses the Phase 2 upgrade with ownership intact. |
| [`tests/unit/test_auth.py`](../tests/unit/test_auth.py), [`tests/integration/test_authenticated_routes.py`](../tests/integration/test_authenticated_routes.py) | edit | Provider/dependency behavior, two-user isolation. |
| [`tests/unit/test_auth_client.py`](../tests/unit/test_auth_client.py), [`tests/e2e/test_authenticated_capture_e2e.py`](../tests/e2e/test_authenticated_capture_e2e.py) | edit | Desktop credentials, capture ownership. |
| [`tests/unit/test_rate_limit.py`](../tests/unit/test_rate_limit.py), [`tests/integration/test_rate_limited_routes.py`](../tests/integration/test_rate_limited_routes.py) | edit | Token math, concurrency, no-side-effect ordering. |
| [`tests/e2e/`](../tests/e2e/) | edit | Re-runs Phase 1/2 flows under authentication. |
| [`src/sra/api/routers/`](../src/sra/api/routers/) | edit | Audits each route's anonymous/user/admin and limiter policy. |
| [`PROGRESS.md`](../PROGRESS.md), [`tickets/README.md`](README.md), [`README.md`](../README.md), [`tests/README.md`](../tests/README.md), [this ticket](SRA-305-phase-3-integration-verification.md) | edit | Evidence, setup, limitations. |

## Acceptance criteria

- [x] A database created at the Phase 2 revision upgrades with existing ownership intact and follows the documented legacy-data procedure without automatic claiming
- [x] The full default suite passes offline, with provider/model/hardware boundaries replaced only where documented; lint and type checks pass
- [ ] Two real Google accounts can sign in and `/auth/me` returns the correct stable application users; logout, expiry, and invalid state fail safely and recover
- [ ] User A and user B create uniquely identifiable entries and cannot retrieve or infer each other's IDs/content through ingestion, search, chat, or digest
- [ ] The real desktop loopback/Keychain flow survives restart and a real capture stores data under the selected account without exposing the credential
- [x] User/admin behavior is correct and operational endpoints reveal aggregates only, never another user's study content
- [x] Authenticated and anonymous limits are independent, return correct `429`/`Retry-After`, block work before side effects, refill/recover, and state the single-process limitation
- [x] Every API route has an audited anonymous/user/admin and limiting policy; health remains usable
- [ ] Failure/recovery checks leave no listener, thread, HTTP client, credential log, temporary audio file, or duplicate submission
- [x] Setup, test, ticket-board, progress, and security-limitation documentation matches observed behavior

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_user_model.py tests/unit/test_auth.py tests/unit/test_auth_client.py tests/unit/test_rate_limit.py -v
pytest tests/integration/test_phase3_migration.py tests/integration/test_authenticated_routes.py tests/integration/test_rate_limited_routes.py -v
pytest tests/e2e/test_authenticated_capture_e2e.py -v
pytest -m phase3 -q
pytest -q
ruff check .
pyright
```

The route-policy audit must fail when a mounted route lacks its intended dependency. Opt-in real Whisper and desktop checks remain in [`tests/README.md`](../tests/README.md).

## Manual verification

- [ ] Rehearse the Phase 2-to-Phase 3 migration and recovery with preserved user/entry/review IDs; sign in as two real Google accounts and verify each `/auth/me`.
- [ ] Through the native desktop app, capture unique notes for A and B; prove exact-ID persistence and isolation through search, chat, and digest both ways.
- [ ] Restart desktop and server; verify Keychain reuse/reauthentication, ownership, and another capture. Exercise admin `403`/`200`, limits, invalid/expired login, cancellation, unavailable server, lost responses.
- [ ] Confirm no listener, thread, HTTP client, credential log, temporary audio file, or duplicate submission remains; record failures, recovery, cleanup, and skipped boundaries without secrets.

**Verification:**
- **2026-10-14, macOS, Python 3.13, single-process servers:** no real browser, Google account, Keychain prompt, microphone, or hotkey; ephemeral loopback ports and disposable SQLite used.
- `pytest -q -rs`: **386 passed, 9 skipped** (eight opt-in audio, one Whisper). `pytest -m phase3 -q`: **112 passed, 283 deselected**. `make test-data`: **394 passed, 1 deselected**; its first run exposed five outdated-harness `401` failures, now fixed without weakening assertions.
- `ruff check .`, `pyright`, and `git diff --check` passed; no commit was made.
- Migration `067a64508521` → `8cb1b420d9d2` preserved user **41**, entry **51** owned by 41, and review **61** linked to 51, including timestamps/date; a separate recovery copy retained the old revision and ownership.
- Synthetic `subject-a`/`subject-b` checks captured exact fresh IDs (normally **1 and 2**), owner IDs, six schedules, broad search, foreign-ID chat, and next-day digest — not real accounts.
- The voice test kept transcription, pipeline, HTTP, auth, graph, repository, schedules, and SQLite real, substituting inference/storage. The desktop roundtrip kept listener, nonce, state/callback, one-use exchange, validation, and selector reuse real; browser, Google identity, and API location were substituted.
- Fixes required the initiating browser's HttpOnly login cookie, desktop nonce, synchronized one-success state/code consumption (16 consumers), expired-state pruning, explicit native Keychain, safe account deletion, and body/exception redaction; no retry added.
- `require_user`/`require_admin` checks returned `403` to users and `200` to admins. Limiter regressions covered independent user/IP buckets, expiry/refill/reset, spoofed forwarding headers, concurrency, `429`/`Retry-After`, and zero writes/model calls after denial.
- [`test_route_policy_audit.py`](../tests/integration/test_route_policy_audit.py) enforces `/api/health` public/exempt; `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` public/exempt; `/api/v1/auth/google/login`, `/api/v1/auth/google/callback`, `/api/v1/auth/desktop/exchange`, `/api/v1/auth/logout` anonymous/IP-limited; `/api/v1/auth/me`, `/api/v1/entries/ingest`, `/api/v1/search`, `/api/v1/chat`, `/api/v1/digest` user-limited; `/api/v1/admin/usage`, `/api/v1/admin/rate-limits` admin/user-limited.
- **Pending acceptance:** two real Google accounts, promoted admin, browser cookie/expiry/logout recovery, real Keychain restart reuse, microphone/global-hotkey/native-UI behavior, and cleanup. Replay stores and rate limits are process-local; bearer logout cannot revoke a stolen stateless credential before expiry; auth timeouts stay 15 seconds; README disables access logging and requires proxy/tracing redaction. Existing logs/Git history were not inspected for live secrets.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
