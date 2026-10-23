# SH-002 — `/api/v1/auth/refresh` route is mounted without an audit policy entry

**Status:** closed

## Bug

`POST /api/v1/auth/refresh` was added in SRA-306 (commit `fd40949`) and is mounted through `sra.api.routers.__init__`, but the explicit policy dictionary inside `tests/integration/test_route_policy_audit.py::test_every_mounted_route_has_an_explicit_authentication_and_limit_policy` was never extended to include it. The mounted-route inventory therefore fails its own check, and the route is silently absent from any future "what auth does this endpoint require?" review built off that dict.

Reproduce: `pytest tests/integration/test_route_policy_audit.py -v` →
`AssertionError: Unaudited route: ('POST', '/api/v1/auth/refresh')`.

Root cause: SRA-306 added the endpoint to satisfy the refresh-token rotation flow, and no test in the SRA-306 focused set exercises the route-policy audit dict — so the audit was not updated in the same commit that mounted the route.

## Files

| File | Change | Why |
|---|---|---|
| `tests/integration/test_route_policy_audit.py` | edit | Add `("POST", "/api/v1/auth/refresh"): "anonymous"` to the `policies` dict. The audit's source of truth lives here; one line brings the inventory back into alignment with the mounted router. |

`anonymous` matches SRA-306's design — the refresh endpoint is the same policy class as `/auth/desktop/exchange` and `/auth/logout`, because it exchanges a refresh token for a new pair and never touches a user bucket.

## Fix

The SRA-805 commit (`d6f080a`) registered the two interview-route entries plus the same `/auth/refresh` entry this ticket named, and the SRA-701 dashboard work made `/static`, `/dashboard-static`, `/`, `/signin`, `/signout`, `/quiz`, `/dashboard`, `/dashboard/interview`, `/settings`, `/healthz`, `/api/v1/me/dev/pregenerate-now`, `/api/v1/reviews/upcoming`, and the framework-static mounts all appear in the inventory.

## Acceptance criteria

- [x] `pytest tests/integration/test_route_policy_audit.py -v` passes
- [x] The full default suite (`pytest -q`) does not regress in count relative to the SH-001 baseline
- [x] Subsequent self-heal runs do not flag a missing `/auth/refresh`

## Verification

- **Date / environment:** 2026-10-16, Python 3.13.12, FastAPI 0.115+ via the in-process `TestClient` from `tests/conftest.py::client`.
- **Commands:** `pytest tests/integration/test_route_policy_audit.py -v`; `pytest -q` (default suite, full run).
- **Result:** the full audit now covers **25 mounted API routes + 4 framework/static paths** and passes — `test_every_mounted_route_has_an_explicit_authentication_and_limit_policy` PASSED.

Run summary and the other closed tickets: [`SH-REPORT.md`](SH-REPORT.md).

---

Design context: [`technical-design.md`](../../docs/design/technical-design.md). Process:
[`guides/self_heal.md`](../../guides/self_heal.md). Source: [`SH-001`](SH-001-coverage-matrix.md).