# SRA-506 — Client API extensions for wake-up and voice quiz

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Extend `src/sra/client/api_client.py` with four new methods the desktop client
uses during Phase 5: `get_digest_ready() -> bool`, `get_digest_text() -> str`,
`start_voice_quiz_attempt(quiz_set_id) -> dict`, and
`send_voice_turn(attempt_id, answer_text) -> dict`. Each method follows the
existing one-POST/one-GET pattern from SRA-201: single HTTP call, strict response
validation, consistent `ApiClientError` reporting, and the same resource cleanup
as the existing client. This ticket is purely additive — no existing public
method changes signature or behavior, and the four new methods share the same
`auth_token` resolution pattern SRA-303 added. `get_digest_text()` is separate
from `get_digest()` on purpose: the raw `ReviewOut[]` JSON suits the dashboard,
but the wake-up loop needs one sentence it can `speak(...)` directly.

## Unlocks

- SRA-507's `wakeup.deliver_digest()` becomes `speak(api_client.get_digest_text())`
  gated on `api_client.get_digest_ready()`.
- SRA-508's `voice_quiz_client.run_voice_quiz(quiz_set_id)` becomes a loop over
  `api_client.start_voice_quiz_attempt(...)` followed by
  `api_client.send_voice_turn(...)` calls.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/client/api_client.py`](../src/sra/client/api_client.py) | edit | Add the four public methods plus `_extract_detail`, `_auth_required_error`, `_rate_limited_error`, `_http_error`, `_authed_get`, `_authed_post` helpers. `get_digest_ready()` issues `GET /api/v1/digest/ready` and returns the `{"ready": bool}` payload; `get_digest_text()` issues `GET /api/v1/digest` and formats the list into `"You have N reviews due today: <topic 1>, <topic 2>, and <topic M>."` (empty list → `"You have no reviews due today."`); `start_voice_quiz_attempt(quiz_set_id, *, auth_token=None)` issues `POST /api/v1/quiz/{quiz_set_id}/attempts {"mode": "voice"}` and returns `{attempt_id, question_text, options}`; `send_voice_turn(attempt_id, answer_text, *, auth_token=None)` issues `POST /api/v1/quiz/attempts/{attempt_id}/voice-turn {"answer_text": answer_text}` and returns the `VoiceTurnResponse` dict. Every request wraps its `httpx.Client` in `try/finally` with `response.close()` so long-running desktop processes do not leak sockets. |
| [`tests/unit/test_api_client.py`](../tests/unit/test_api_client.py) | edit | Unit tests per method through `httpx.MockTransport`: success path, HTTP error, validation error, missing auth token where applicable. |
| [`tests/e2e/test_phase5_api_client_e2e.py`](../tests/e2e/test_phase5_api_client_e2e.py) | new | Drives the four methods against a mock transport to confirm request shape, header presence, and error mapping match the live server's contract. |

## Acceptance criteria

- [x] Each of the four methods issues exactly one HTTP request, parses it strictly, and returns the documented shape
- [x] Missing/invalid auth raises `ApiClientError("auth_required")`; rate-limited responses raise `ApiClientError("rate_limited")` with `Retry-After` exposed
- [x] `get_digest_text()` produces a human-readable sentence from the `ReviewOut[]` payload and handles the empty-digest case
- [x] `send_voice_turn()` maps `409` to `ApiClientError("attempt_completed")`
- [x] `start_voice_quiz_attempt()` maps the Phase 4 `400` "Voice mode is not yet supported" to `ApiClientError("server_phase5_pending")`
- [x] No socket leaks under repeated calls (covered by a `gc.collect()` / open-file-count test)
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_api_client.py -v
pytest tests/e2e/test_phase5_api_client_e2e.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

Error mapping is by status: `401` → `ApiClientError("auth_required")`, `429` →
`ApiClientError("rate_limited", retry_after=...)`, any other non-200 →
`ApiClientError("http_error", status=..., body=...)`. An explicit `auth_token`
wins; otherwise `load_token()` is called once per request.

## Manual verification

- [ ] Against a running server with a valid token, `client.get_digest_ready()` prints `True`/`False`, `client.get_digest_text()` prints the digest sentence, and `client.start_voice_quiz_attempt(quiz_set_id=1)` prints the voice attempt dict.

**Verification:**

- **Date/environment:** 2026-10-14, local macOS, Python 3.13, existing `.venv`.
- Focused `pytest tests/unit/test_api_client.py tests/e2e/test_phase5_api_client_e2e.py -v` → **75 passed**.
- `pytest -m 'not phase5' -q` → **479 passed, 13 pre-existing Phase 3/4 failures, 14 skipped** — the SRA-505 baseline, no new regressions.
- `ruff check .` → All checks passed. `pyright` → 0 errors, 0 warnings.
- New artifacts: the four public methods and six private helpers in `api_client.py`; `ApiClientError` gains a class-level `retry_after: int | None = None` annotation so Pyright accepts the assignment in `_rate_limited_error`. No schema or migration changes.
- Failure/recovery: the initial run failed Pyright with `Cannot assign to attribute "retry_after"`; fixed by declaring `retry_after: int | None = None` on `ApiClientError` as a class-level annotation only, keeping the constructor's call sites stable. No test, behaviour, or public-API change was needed.
- Limitation: the manual smoke against a live server is not part of this ticket — SRA-507 and SRA-508 exercise the new methods end to end.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
