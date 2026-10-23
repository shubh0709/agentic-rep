# SH-005 — `MemoryCredentialStore` test fake in `tests/unit/test_auth_client.py` does not implement `save_pair`

**Status:** closed

## Bug

`sra.client.auth_client.CredentialStore` (protocol) declares `load`, `save_pair`, and `delete` since SRA-306. The in-memory test fake `MemoryCredentialStore` at `tests/unit/test_auth_client.py:30` declares `load`, `save`, and `delete`, missing the new `save_pair` member. Pyright flags this eight times (`tests/unit/test_auth_client.py:78, 92, 103, 200, 224, 253, 358, …`).

At runtime every test still passes, because the affected call sites (`sign_in`, `sign_out`, `load_authenticated_account`) only use the methods the fake provides — the protocol mismatch surfaces only in static analysis:

```
MemoryCredentialStore is incompatible with protocol "CredentialStore"
"save_pair" is not present (reportArgumentType)
```

Root cause: SRA-306 added `save_pair` to the protocol without updating the older Phase 3 test fake. A second `MemoryCredentialStore` in `tests/unit/test_api_client_refresh.py:16` already implements `save_pair` correctly, which confirms the protocol is intentional and the older fake was simply left behind.

## Files

| File | Change | Why |
|---|---|---|
| `tests/unit/test_auth_client.py` | edit | Add a `save_pair` method to `MemoryCredentialStore` mirroring the existing `save` shape, including kwargs `server_origin`, `account`, `credential`, `refresh_credential`. Restores protocol conformance without altering tested behavior. |

`tests/unit/test_api_client_refresh.py` is left untouched — its companion fake already conforms.

## Fix

Every in-memory store used in tests is now structurally a `CredentialStore`. The fake can no-op `save_pair` because the affected tests do not exercise the refresh path; production sites use real implementations.

## Acceptance criteria

- [x] `pyright tests/unit/test_auth_client.py` reports zero errors
- [x] Every Phase 3 (`sign_in`, `sign_out`, `load_authenticated_account`) test still passes unchanged in count
- [x] The companion fake in `tests/unit/test_api_client_refresh.py` is untouched

## Verification

- **Date / environment:** 2026-10-16, Python 3.13.12.
- **Closed by:** commit `2d33a4e test(auth): pin CredentialStore.save_pair test double (SRA-306 follow-up)`. The fake gained a `pair_saves: list[tuple[server_origin, account, credential, refresh_credential]]` field and a `save_pair(*, server_origin, account, credential, refresh_credential)` method that mirrors `save` for the active credential and records the refresh pair for test inspection.
- `pytest tests/unit/test_auth_client.py -v` → **26/26 PASS**.
- `pyright tests/unit/test_auth_client.py` → **0 errors (8 → 0)**.
- `pytest tests/unit/test_api_client_refresh.py -v` (the companion refresh-path tests with their own `MemoryCredentialStore`) → unchanged, all green.
- **Behavior change:** none at runtime. The production `MacOSKeychainCredentialStore.save_pair` is untouched and remains the real implementation; the fake simply satisfies the protocol so `reportArgumentType` does not fire when the Phase 3 tests pass it into `sign_in` / `sign_out` / `load_authenticated_account`.

Run summary: [`SH-REPORT.md`](SH-REPORT.md).

---

Design context: [`technical-design.md`](../../docs/design/technical-design.md). Process:
[`guides/self_heal.md`](../../guides/self_heal.md). Source: [`SH-001`](SH-001-coverage-matrix.md).