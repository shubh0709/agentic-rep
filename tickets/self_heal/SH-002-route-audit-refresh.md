SH-002 — `/api/v1/auth/refresh` route is mounted without an audit policy entry

**Type:** Bug (missing audit registration)
**Source:** Application Self-Heal pass 1 (`tickets/self_heal/SH-001-coverage-matrix.md`)
**Priority:** High (route-policy audit is a safety guard; a missing entry means a newly-mounted route is not centrally inventoried)
**Status:** Confirmed

## Summary

`POST /api/v1/auth/refresh` was added in SRA-306 (commit `fd40949`) and is mounted
through `sra.api.routers.__init__`, but the explicit policy dictionary inside
`tests/integration/test_route_policy_audit.py::test_every_mounted_route_has_an_explicit_authentication_and_limit_policy`
was never extended to include it. As a result the mounted-route inventory fails
its own check, and the route is silently absent from any future "what auth does
this endpoint require?" review built off that dict.

## Preconditions / minimal reproduction

1. Start the test app.
2. `pytest tests/integration/test_route_policy_audit.py -v`
3. Observe `AssertionError: Unaudited route: ('POST', '/api/v1/auth/refresh')`.

## Expected behavior

The audit dictionary lists every mounted route. New routes are added in the
same commit that mounts them so the inventory stays a single source of truth.

## Actual behavior (sanitized)

```
E       AssertionError: Unaudited route: ('POST', '/api/v1/auth/refresh')
```

## Suspected root cause

SRA-306 added the `POST /auth/refresh` endpoint to satisfy the refresh-token
rotation flow. The audit was not updated in that commit because no test in the
SRA-306 focused set exercises the route-policy audit dict.

## Acceptance criteria

- [ ] `pytest tests/integration/test_route_policy_audit.py -v` passes.
- [ ] The full default suite (`pytest -q`) does not regress in count relative
      to the SH-001 baseline.
- [ ] Subsequent self-heal runs do not flag a missing `/auth/refresh`.

## Files to change

| File | Action | Reason |
|---|---|---|
| `tests/integration/test_route_policy_audit.py` | add `("POST", "/api/v1/auth/refresh"): "anonymous"` to the `policies` dict | The audit's source of truth lives here; adding the entry is a one-line edit that brings the inventory back into alignment with the mounted router. |

`anonymous` matches SRA-306's design — the refresh endpoint is the same
policy class as `/auth/desktop/exchange` and `/auth/logout` because it
exchanges a refresh token for a new pair and never touches a user bucket.

## Verification record

- **Date / environment:** 2026-10-16, Python 3.13.12, FastAPI 0.115+ via
  the in-process `TestClient` from `tests/conftest.py::client`.
- **Commands:**
  - `pytest tests/integration/test_route_policy_audit.py -v`
  - `pytest -q` (default suite, full run)
- **Closed by the same edit:** the `SRA-805` commit (`d6f080a`)
  registered the two interview-route entries plus the same `/auth/refresh`
  entry this ticket named, and the SRA-701 dashboard work made the
  `/static`, `/dashboard-static`, `/`, `/signin`, `/signout`, `/quiz`,
  `/dashboard`, `/dashboard/interview`, `/settings`, `/healthz`,
  `/api/v1/me/dev/pregenerate-now`, `/api/v1/reviews/upcoming`,
  framework-static mounts all appear in the inventory. The full audit
  now covers **25 mounted API routes + 4 framework/static paths** and
  passes.
- **Result:** `tests/integration/test_route_policy_audit.py::test_every_mounted_route_has_an_explicit_authentication_and_limit_policy PASSED`.
