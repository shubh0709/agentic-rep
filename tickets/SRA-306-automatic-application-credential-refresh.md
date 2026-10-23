# SRA-306 — Automatic application credential refresh

**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting · **Status:** implemented

## Summary

Extend the signed application credential with a long-lived refresh credential and a short-lived access credential. `POST /auth/refresh` consumes and rotates the old `jti`, returning a new pair; only the access credential satisfies `require_user`. Browser and desktop clients refresh once on a study-route `401`, then retry the original request once. Replay revokes the user's refresh family. Real Google, two-tab browser, native Keychain, and physical-desktop acceptance remain pending.

## Unlocks

- Active browser and desktop sessions survive access-credential expiry without forcing Google consent again, while abandoned sessions still require reauthentication after the refresh window.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/auth/google_oauth.py`](../src/sra/auth/google_oauth.py) | edit | Adds `issue_refresh_credential(user_id, jti, *, signing_secret, ttl_seconds, now)`, `verify_refresh_credential(credential, *, signing_secret, now) -> RefreshClaims`, `RefreshClaims`, `_CREDENTIAL_REFRESH_PURPOSE`, locked `RefreshTokenStore` (`issue`/`consume`/`revoke_family(user_id)`) mirroring `OAuthStateStore`/`CompletionCodeStore`; `verify_app_credential` rejects refresh payloads. |
| [`src/sra/auth/dependencies.py`](../src/sra/auth/dependencies.py) | edit | `require_user` stays access-only; `require_refresh_token` verification stays local to the route. |
| [`src/sra/api/routers/auth.py`](../src/sra/api/routers/auth.py) | edit | `/auth/google/callback` and `/auth/desktop/exchange` issue both credentials; `POST /auth/refresh` rotates the pair and cookies; `POST /auth/logout` clears both; `CredentialResponse` gains `refresh_credential` and `refresh_expires_in`. |
| [`src/sra/config/`](../src/sra/config/), [`.env.example`](../.env.example) | edit | Adds `auth_refresh_ttl_seconds`; `AUTH_REFRESH_TTL_SECONDS` defaults to `604800`, `AUTH_CREDENTIAL_TTL_SECONDS` stays `3600`; rotating `AUTH_SIGNING_SECRET` invalidates every credential. |
| [`web/static/js/auth-fetch.js`](../web/static/js/auth-fetch.js) | new | Shared `window.sraJsonFetch` helper: one refresh, one retry. |
| [`web/static/js/dashboard.js`](../web/static/js/dashboard.js), [`web/static/js/quiz.js`](../web/static/js/quiz.js), [`web/static/js/settings.js`](../web/static/js/settings.js) | edit | Use the shared helper; keep the sign-in redirect after a failed refresh. |
| [`src/sra/client/api_client.py`](../src/sra/client/api_client.py), [`src/sra/client/auth_client.py`](../src/sra/client/auth_client.py) | edit | `ingest_transcript` refreshes once via `_post_with_refresh`, persists `save_pair(server_origin, account, credential, refresh_credential)` in Keychain, retries once; `load_refresh_credential` survives restart. |
| [`tests/unit/test_auth_refresh.py`](../tests/unit/test_auth_refresh.py), [`tests/integration/test_auth_refresh_routes.py`](../tests/integration/test_auth_refresh_routes.py), [`tests/unit/test_api_client_refresh.py`](../tests/unit/test_api_client_refresh.py) | new | Issuance, purpose separation, rotation, replay revocation, cookies, bearer flow, Keychain persistence. |
| [`README.md`](../README.md) | edit | Access/refresh split, TTL, security boundary. |

## Acceptance criteria

- [ ] `/auth/google/callback` and `/auth/desktop/exchange` issue both an access credential and a refresh credential; both share the same `sub` and the refresh credential has its own `jti`
- [ ] `/auth/refresh` accepts a valid refresh cookie or `Bearer <refresh_credential>`, returns a new access + refresh pair, rotates the `jti`, and on a second submission of a consumed `jti` revokes the whole refresh chain for that user
- [ ] A refresh credential presented to a study endpoint is rejected with `401`; the access credential is the only path that satisfies `require_user`
- [ ] The browser dashboard, quiz, and settings pages continue to make study requests across an access-token expiry without a page reload or a redirect to `/signin`, and they fall back to the existing redirect only when the refresh token itself is gone
- [ ] The desktop client captures across a refresh without re-running Google sign-in, and surfaces the existing "sign in again" path only after the refresh window has lapsed
- [ ] A stolen refresh credential used by the attacker is invalidated the moment the legitimate client performs its next refresh, and the legitimate client is notified through the existing `401`-then-redirect path on its next page load
- [ ] Logout clears both the access cookie and the refresh cookie; the desktop Keychain entry is removed by the existing `Sign out` action after the client change persists both fields
- [ ] `AUTH_REFRESH_TTL_SECONDS` is configurable, defaults to 604,800 (seven days), and is rejected at startup if non-positive
- [ ] The full Phase 1–6 regression suite stays green; focused auth tests cover issuance, rotation, reuse detection, and the rate-limit inheritance

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_auth_refresh.py tests/integration/test_auth_refresh_routes.py tests/unit/test_api_client_refresh.py tests/unit/test_config.py -v
pytest tests/unit/test_auth.py tests/integration/test_authenticated_routes.py tests/unit/test_api_client.py tests/unit/test_auth_client.py tests/integration/test_route_policy_audit.py -q
pytest tests/unit/test_auth.py -v
pytest tests/integration/test_authenticated_routes.py -v
pytest tests/unit/test_api_routes.py tests/unit/test_ingest_route.py -v
pytest -q
ruff check .
pyright src/sra/auth/google_oauth.py src/sra/auth/dependencies.py src/sra/api/routers/auth.py src/sra/config/ src/sra/client/api_client.py src/sra/client/auth_client.py
```

Use the controlled `_clock` past access expiry; a replayed old `jti` must revoke the family. Google, native Keychain, and browser delivery remain substituted until manual acceptance.

## Manual verification

- [ ] With short access/refresh TTLs, keep a dashboard tab active past access expiry and open a second tab; verify both continue without Google consent or `/signin` redirect.
- [ ] Capture with the desktop before and after access expiry; verify no Google prompt, then let the refresh TTL lapse and verify the existing "sign in again" state recovers after sign-in. Restore defaults.
- [ ] With a real Google test application and native Keychain, record cookie flags, two-tab rotation, desktop restart reuse, logout, expiry, and recovery without recording secrets.

**Verification:**
- **2026-10-15:** implementation landed. `Settings.auth_refresh_ttl_seconds` = `604800`; `.env.example` documents `AUTH_REFRESH_TTL_SECONDS=604800`; `AUTH_CREDENTIAL_TTL_SECONDS` stays `3600`.
- `POST /auth/refresh` accepts the `sra_refresh` HttpOnly cookie or bearer, rotates cookies/tokens, returns `401 "Sign in again to continue"` on replay; logout clears both `sra_session` and `sra_refresh`.
- `require_user` unchanged. `_post_with_refresh` refreshes once and retries once; `save_pair(server_origin, account, credential, refresh_credential)` persists both Keychain values; `load_refresh_credential` supports restart.
- `window.sraJsonFetch` refreshes once and retries once; dashboard, quiz, and settings redirect only after a second `401`. The cookie parser was corrected to `http.cookies.SimpleCookie`; assertions were not weakened.
- Focused SRA-306 command: **27 passed** (11 unit, 8 integration, 3 desktop, 5 config). Phase 3 regression: **126 passed, 0 failed**; route audit includes `("POST", "/api/v1/auth/refresh"): "anonymous"`.
- Full `pytest -q`: **797 passed, 14 skipped, 23 failed**; all 23 documented SRA-406/SRA-509 Phase 4/5 limitations, not from SRA-306. `ruff check .` passed; scoped `pyright`: **0 errors, 0 warnings**.
- Automated clock, route, and client checks proved rotation, expired-access recovery, and replay-family revocation. Real Google, two-tab browser, native Keychain, and physical desktop evidence remains pending.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
