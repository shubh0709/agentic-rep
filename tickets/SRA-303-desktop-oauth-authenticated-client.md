# SRA-303 — Desktop OAuth and authenticated capture client

**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting · **Status:** implemented

## Summary

Add macOS desktop sign-in: open Google consent in the system browser, receive a short-lived completion code on a temporary loopback server, exchange it for the server-issued application credential, and store that credential in Keychain. The client validates it through `/auth/me` and attaches it to the existing HTTP client. Before this ticket, protected ingestion returns `401`; afterwards, typed and spoken notes persist under the signed-in Google-backed account. Google authorization codes, identity tokens, secrets, and bearer credentials never enter configuration or logs. Automated coverage is complete; real browser, Keychain, and physical-capture evidence remains pending.

## Unlocks

- A user can sign in once, capture a note, and retrieve the exact authenticated entry IDs while retaining Phase 2's one-submission and uncertain-outcome guarantees.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/client/auth_client.py`](../src/sra/client/auth_client.py) | edit | Owns the bounded loopback/browser exchange, `/auth/me` validation, Keychain selection, and cleanup. |
| [`src/sra/client/api_client.py`](../src/sra/client/api_client.py) | edit | Adds the bearer header without changing response validation, timeouts, or the no-automatic-POST-retry rule. |
| [`src/sra/config/`](../src/sra/config/) | edit | Holds client-only login timeout and safe Keychain/service settings; never server OAuth secrets. |
| [`src/sra/capture/menubar_app.py`](../src/sra/capture/menubar_app.py) | edit | Adds nonblocking sign-in, sign-out, account feedback, expiry recovery, and quit cancellation. |
| [`src/sra/capture/pipeline.py`](../src/sra/capture/pipeline.py) | edit | Remains the one-transcript-to-one-HTTP-call boundary; it does not learn OAuth details. |
| [`pyproject.toml`](../pyproject.toml), [`requirements-lock.txt`](../requirements-lock.txt) | edit | Declares and pins any direct secure-storage dependency. |
| [`tests/unit/test_auth_client.py`](../tests/unit/test_auth_client.py), [`tests/unit/test_api_client.py`](../tests/unit/test_api_client.py), [`tests/unit/test_menubar_app.py`](../tests/unit/test_menubar_app.py) | edit | Cover exchange, redaction, header behavior, UI handoff, cancellation, and cleanup. |
| [`tests/e2e/test_authenticated_capture_e2e.py`](../tests/e2e/test_authenticated_capture_e2e.py) | edit | Drives a stored credential through the protected API, graph, repository, schedules, and search. |
| [`README.md`](../README.md), [`.env.example`](../.env.example) | edit | Explain desktop sign-in, Keychain prompts, logout, expiry, and client/server configuration. |

## Acceptance criteria

- [ ] Desktop login binds loopback only on localhost, exchanges one valid short-lived completion code, rejects replay, and releases every resource on all exits
- [ ] The application credential is stored in Keychain per server/account and is absent from URLs, config files, UI errors, and captured logs
- [ ] `/auth/me` validates a loaded credential; missing/expired/wrong-server credentials produce a clear sign-in requirement and recovery succeeds
- [ ] Typed and spoken ingestion send one authenticated POST and persist entries under the signed-in user, with exact IDs verifiable through authenticated search
- [ ] Authentication failures never trigger an automatic duplicate POST; Phase 2 timeout/response-shape semantics remain intact
- [ ] Sign-in, sign-out, capture, and quit remain responsive and do not leak listeners, threads, credentials, audio files, or HTTP clients
- [ ] Default tests use fake browser/Keychain/provider boundaries; real macOS/browser evidence is recorded separately

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_auth_client.py tests/unit/test_api_client.py tests/unit/test_menubar_app.py -v
pytest tests/e2e/test_authenticated_capture_e2e.py -v
pytest tests/e2e/test_client_ingestion_e2e.py tests/e2e/test_terminal_capture_e2e.py -v
ruff check .
pyright
```

Default checks substitute Cocoa, browser delivery, Google identity, and Keychain storage. A stable CI check may bind a real ephemeral localhost socket while keeping those boundaries substituted.

## Manual verification

- [ ] With a test Google application and disposable database, complete browser sign-in; record macOS/browser/server origin, redacted `/auth/me`, and the Keychain service/account convention.
- [ ] Submit a uniquely worded typed note and, when hardware acceptance is available, a spoken note; record exact IDs and retrieve them through authenticated search.
- [ ] Restart the app, sign out and recover, cancel a login, and force a loopback timeout; confirm no secret appears and the listener port is released.

**Verification:**
- **2026-10-14:** localhost tests preserved server-bound nonce binding and covered success, drip-fed incomplete headers, absolute timeout, cancellation, and listener/watchdog-thread cleanup.
- The automated desktop roundtrip kept the real listener, server state/callback, one-use exchange, `/auth/me` validation, and stored-selector reuse real; browser delivery, Google identity, HTTP location, and Keychain backend were substituted.
- Menu-adapter tests verified quit cancellation and restored-token forwarding; the stored-credential voice E2E kept the real transcription wrapper, pipeline, HTTP client, auth dependencies, graph, repository, schedules, and SQLite, with inference substituted.
- Native Keychain selection was explicit: removing account A preserved active account B. Authenticated failures suppressed upstream secret text, retained HTTP status and one-POST uncertainty, and never retried.
- SRA-305's 2026-10-14 runs recorded `pytest -q -rs`: **386 passed, 9 skipped**; `pytest -m phase3 -q`: **112 passed, 283 deselected**; `make test-data`: **394 passed, 1 deselected**. Real Google/Keychain/desktop evidence was not claimed.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
