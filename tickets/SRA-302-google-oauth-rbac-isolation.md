# SRA-302 — Google OAuth, authenticated isolation, and RBAC

**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting · **Status:** implemented

## Summary

Implement server-side Google OAuth for browser login, issue signed application
credentials, replace automatic local-user resolution with `require_user`, and prove
every study route operates only on the authenticated user's data. Add
`require_admin` plus a minimal usage endpoint so the two roles have observable
behavior without exposing anyone's study content. Before this ticket, a request with
no credentials is silently treated as the first local user; after it, unauthenticated
study requests return `401` and two signed-in users see disjoint results.

## Unlocks

- A user can sign in through Google, call `/auth/me`, submit a transcript with the
  issued session, and find only the new IDs in their own search. A second user sees
  different results even for a broad query. Logout invalidates the session. An admin
  reads instance-level counts; an ordinary user gets `403`.
- SRA-303 consumes the bearer flow; SRA-304 adds the cost bound.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/auth/google_oauth.py`](../src/sra/auth/google_oauth.py) | edit | Build authorization URLs from validated settings with a fresh signed/expiring `state`; exchange one-time codes; verify signature, issuer, audience, expiry, and subject; upsert by `google_sub`. |
| [`src/sra/auth/dependencies.py`](../src/sra/auth/dependencies.py) | edit | `require_user` accepts a signed web session cookie **or** an application bearer token; add composed `require_admin`. Invalid, expired, or missing credentials return `401` — never restore the auto-created local user. |
| [`src/sra/api/routers/auth.py`](../src/sra/api/routers/auth.py) | edit | Login, callback, one-use desktop completion-code exchange, logout, `/auth/me`. |
| [`src/sra/api/routers/admin.py`](../src/sra/api/routers/admin.py) | edit | Minimal `/admin/usage` returning aggregate counts only, never study content. |
| [`entries.py`](../src/sra/api/routers/entries.py), [`digest.py`](../src/sra/api/routers/digest.py), [`search.py`](../src/sra/api/routers/search.py), [`chat.py`](../src/sra/api/routers/chat.py) | edit | Replace the placeholder dependency at every protected entry point; pass only the resolved `user.id`. |

## Acceptance criteria

- [ ] Authorization URLs contain a fresh protected state and the configured
  callback; invalid callback state is rejected before code exchange
- [ ] Verified first login creates one user; repeat login by the same `google_sub`
  updates allowed profile fields without changing role, ID, or ownership
- [ ] A desktop callback carries only a short-lived one-use completion code;
  exchange returns the bearer once, replay fails, and credentials never appear in URLs
- [ ] Missing, invalid, expired, or conflicting credentials return `401`; valid
  cookie and bearer credentials resolve the same user contract
- [ ] Every study endpoint is protected and two authenticated users cannot ingest,
  search, chat about, or digest each other's data
- [ ] Ordinary users receive `403` from admin usage; admins receive aggregates with
  no study content
- [ ] Logout clears the session, auth/provider failures are recoverable, and
  secrets/tokens never appear in responses or logs
- [ ] Default automated tests remain offline; a real-Google check is recorded when run

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_auth.py tests/integration/test_authenticated_routes.py -v
pytest tests/unit/test_api_routes.py tests/unit/test_ingest_route.py -v
pytest -q && ruff check . && pyright
```

The integration flow uses two signed application identities, the real FastAPI
dependencies/routes, a temporary database, and fake Google/model network boundaries.
Assert exact newly created IDs and broad-query isolation, not nonempty responses.

Google proves the external identity; this server issues and validates its own
short-lived application credential. Never use an unverified claim, request-body user
ID, email, or desktop-supplied numeric ID as authority. OAuth `state` binds the
callback to the login attempt and prevents login CSRF; it is **not** the access
credential, and Google tokens must not reach the browser or cookies.
>
> For desktop login, redirect only a short-lived, single-use completion code to
> localhost, then exchange it for the bearer credential — rather than putting a
> bearer token in the loopback query string, where history and logs could expose it.
> Cookie flags must match deployment reality: HTTP-only and SameSite are mandatory,
> `Secure` is mandatory outside documented localhost development, and logout clears
> the cookie.

## Manual verification

- [ ] Configure a Google OAuth test application with the exact local callback URL
      and a disposable database. Sign in as user A, call `/auth/me`, ingest a
      uniquely tagged note, record its returned IDs. Sign out and verify the session
      is rejected. Sign in as user B and confirm search, chat, and digest cannot
      reveal user A's marker or IDs; create a second note and confirm the reverse.
- [ ] Promote only the intended test account to `admin` through the documented
      procedure. Confirm the ordinary account receives `403`, the admin sees aggregate
      usage with no topics, summaries, transcripts, or profile details. Break the
      callback/secret to observe a safe failure, then verify login recovers.

### Verification record

- **2026-10-14**, offline test environment, localhost callback, default application
  credential lifetime **3,600 seconds**. Synthetic verified subjects only — no real
  Google account was contacted. Isolated temporary SQLite IDs were asserted exactly
  and discarded after each test. Broad search and due-review digest returned only
  the credential owner's rows. Ordinary user received `403`; administrator received
  counts only; logout cleared the cookie and the next `/auth/me` was `401`.
- **51 SRA-302 checks passed; 272 Phase 1/2 checks passed with 9 environment skips.**
  Ruff and Pyright passed. SRA-303 and SRA-304 tests remained intentionally red and
  are outside this ticket.
- **SRA-305 follow-up (2026-10-14):** web callbacks now require the initiating
  browser's HttpOnly login cookie, and desktop callbacks require the client's random
  attempt binding; the regression test rejects a foreign browser/nonce before provider
  exchange and proves a fresh attempt recovers. OAuth-state and completion-code
  consumption are synchronized — sixteen concurrent consumers produce exactly one
  success. Expired pending state is pruned on subsequent issue.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).