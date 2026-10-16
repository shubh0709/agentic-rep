SH-005 — `MemoryCredentialStore` test fake in `tests/unit/test_auth_client.py` does not implement `save_pair`

**Type:** Test / expectation defect (pyright-only complaint; runtime code is correct)
**Source:** Application Self-Heal pass 1 (`tickets/self_heal/SH-001-coverage-matrix.md`)
**Priority:** Low (does not affect user-visible behavior; cleanup so the test fake matches the `CredentialStore` protocol introduced in SRA-306)
**Status:** Confirmed

## Summary

`sra.client.auth_client.CredentialStore` (protocol) declares `load`,
`save_pair`, and `delete` since SRA-306. The in-memory test fake
`MemoryCredentialStore` in `tests/unit/test_auth_client.py:30` declares
`load`, `save`, and `delete`, missing the new `save_pair` member. Pyright
flags this eight times (`tests/unit/test_auth_client.py:78, 92, 103, 200, 224,
253, 358, …`). At runtime every test still passes because the affected
call sites (`sign_in`, `sign_out`, `load_authenticated_account`) only use
the methods the fake provides; the protocol mismatch only surfaces in
static analysis.

A second `MemoryCredentialStore` in
`tests/unit/test_api_client_refresh.py:16` already implements `save_pair`
correctly for the refresh-specific tests, which confirms the protocol is
intentional and the older fake simply got left behind.

## Preconditions / minimal reproduction

1. `pyright tests/unit/test_auth_client.py`
2. Eight `MemoryCredentialStore is incompatible with protocol "CredentialStore" — "save_pair" is not present` errors.

## Expected behavior

Every in-memory store used in tests is structurally a `CredentialStore`. The
fake can no-op `save_pair` because the affected tests do not exercise the
refresh path; production sites use real implementations.

## Actual behavior (sanitized)

```
MemoryCredentialStore is incompatible with protocol "CredentialStore"
"save_pair" is not present (reportArgumentType)
```

## Root cause

SRA-306 added `save_pair` to the protocol without updating the older test
fake from Phase 3.

## Acceptance criteria

- [ ] `pyright tests/unit/test_auth_client.py` reports zero errors.
- [ ] Every Phase 3 (`sign_in`, `sign_out`, `load_authenticated_account`)
      test still passes unchanged in count.
- [ ] The companion fake in `tests/unit/test_api_client_refresh.py` is
      untouched.

## Files to change

| File | Action | Reason |
|---|---|---|
| `tests/unit/test_auth_client.py` | add a `save_pair` no-op method to `MemoryCredentialStore` (mirroring the existing `save` shape, including kwargs `server_origin`, `account`, `credential`, `refresh_credential`). | Restores protocol conformance without altering tested behavior; matches the SRA-306 surface. |

## Verification record

- **Date / environment:** 2026-10-16, Python 3.13.12.
- **Closed by:** commit `2d33a4e test(auth): pin CredentialStore.save_pair
  test double (SRA-306 follow-up)`. The MemoryCredentialStore fake
  gained a `pair_saves: list[tuple[server_origin, account, credential,
  refresh_credential]]` field and a `save_pair(*, server_origin,
  account, credential, refresh_credential)` method that mirrors
  `save` for the active credential and records the refresh pair for
  test inspection.
- **Commands:**
  - `pytest tests/unit/test_auth_client.py -v` → 26/26 PASS.
  - `pyright tests/unit/test_auth_client.py` → 0 errors (8 → 0).
  - `pytest tests/unit/test_api_client_refresh.py -v` (the companion
    refresh-path tests with their own `MemoryCredentialStore`) →
    unchanged: all green.
- **Behavior change:** none at runtime — the production
  `MacOSKeychainCredentialStore.save_pair` is untouched and continues
  to be the real implementation. The fake simply satisfies the
  protocol so pyright's `reportArgumentType` doesn't fire when the
  Phase 3 tests pass the fake into `sign_in` / `sign_out` /
  `load_authenticated_account`.
