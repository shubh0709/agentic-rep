# SRA-506 — Client API extensions for wake-up and voice quiz

**Type:** Task
**Phase:** 5 — Scheduled wake-up & voice quiz
**Priority:** High — the desktop wake-up loop and voice quiz driver both call these methods
**Status:** Implemented — 2026-10-14
**Blocked by:** SRA-502 (digest/ready), SRA-505 (voice-turn + voice start)
**Blocks:** SRA-507, SRA-508

## Summary

Extend `src/sra/client/api_client.py` with four new methods the desktop client uses during Phase 5: `get_digest_ready() -> bool`, `get_digest_text() -> str`, `start_voice_quiz_attempt(quiz_set_id) -> dict`, and `send_voice_turn(attempt_id, answer_text) -> dict`. Each method follows the existing one-POST/one-GET pattern from SRA-201: single HTTP call, strict response validation, consistent `ApiClientError` reporting, and the same resource cleanup as the existing client.

This ticket is purely additive. No existing public method changes signature or behavior; the four new methods share the same `auth_token` resolution pattern SRA-303 added.

## Where things stand right now

- `src/sra/client/api_client.py` exposes `ingest_transcript(transcript) -> list[dict]`, `get_search_results(query) -> list[dict]`, `get_digest() -> list[dict]`, and the auth helpers (`load_token`, `login_via_browser`, etc.) from SRA-201/SRA-303.
- `src/sra/capture/wakeup.py` does not exist yet (SRA-507). It is expected to call `api_client.get_digest_ready()` and `api_client.get_digest_text()`.
- `src/sra/capture/voice_quiz_client.py` does not exist yet (SRA-508). It is expected to call `api_client.start_voice_quiz_attempt(...)` and `api_client.send_voice_turn(...)`.

## What this ticket unlocks

Once SRA-506 lands, SRA-507 and SRA-508 have real client methods to call:

- `wakeup.deliver_digest()` becomes `speak(api_client.get_digest_text())` gated on `api_client.get_digest_ready()`.
- `voice_quiz_client.run_voice_quiz(quiz_set_id)` becomes a `while` loop over `api_client.start_voice_quiz_attempt(...)` followed by `api_client.send_voice_turn(...)` calls.

Before SRA-506: the desktop loop and voice driver have no HTTP client to call, so they cannot be implemented at all. After SRA-506: those callers are pure composition.

## How this fits into the whole app

The four methods slot into `api_client.py` next to the existing entry/search/digest methods, using the same `auth_token` resolution (load from Keychain via `load_token()`, fall back to anonymous if missing — voice quiz and digest both require auth and raise `ApiClientError` if no token is present). The resource cleanup pattern (`try/finally` around the response object, with `response.close()`) is reused so the methods do not leak sockets in long-running desktop processes.

```mermaid
flowchart LR
    subgraph Client[Desktop client]
        Wakeup["capture/wakeup.py<br/>(SRA-507)"]
        VoiceQuiz["capture/voice_quiz_client.py<br/>(SRA-508)"]
        Client["client/api_client.py<br/>get_digest_ready, get_digest_text,<br/>start_voice_quiz_attempt, send_voice_turn"]
    end
    subgraph Server[FastAPI]
        Digest["/api/v1/digest, /digest/ready"]
        Quiz["/api/v1/quiz/{id}/attempts,<br/>/api/v1/quiz/attempts/{id}/voice-turn"]
    end
    Wakeup --> Client
    VoiceQuiz --> Client
    Client -->|"GET, POST"| Digest
    Client -->|"POST"| Quiz
```

The client is the only place on the desktop that knows the server URL — every method derives its base URL from `Settings.server_base_url` (SRA-201) and the `auth_token` from the existing resolver.

### Why a separate `get_digest_text` rather than reusing `get_digest`?

`get_digest()` returns the raw `ReviewOut[]` JSON the dashboard already uses. The wake-up loop needs a *single sentence* it can `speak(...)` directly — joining topics + interval labels into one string. Doing that formatting in the server would couple the server to the desktop's audio output; doing it in the wake-up loop would force it to know about `ReviewOut` shapes. A thin `get_digest_text()` method on the client keeps the server shape-agnostic and keeps the wake-up loop one-liner.

## Files and why they matter

| File | Role and required change |
|---|---|
| [`src/sra/client/api_client.py`](../src/sra/client/api_client.py) | Add four public methods: `get_digest_ready()`, `get_digest_text()`, `start_voice_quiz_attempt(quiz_set_id, *, auth_token=None)`, `send_voice_turn(attempt_id, answer_text, *, auth_token=None)`. Reuse the existing `_request(method, path, body, auth_token)` helper. |
| [`tests/unit/test_api_client.py`](../tests/unit/test_api_client.py) | Add unit tests for each of the four methods, mirroring the existing `test_api_client.py` patterns: success path, HTTP error, validation error, missing auth token (where applicable). |
| [`tests/e2e/test_phase5_api_client_e2e.py`](../tests/e2e/test_phase5_api_client_e2e.py) **[NEW]** | End-to-end test that drives the four methods against a `httpx` mock transport to confirm request shape, header presence, and error mapping match the live server's contract. |

## TDD starting point

The existing `tests/unit/test_api_client.py` is the template — it already covers `ingest_transcript` and `get_digest` through `httpx.MockTransport`. The four new methods follow the same shape: a `MockTransport` returning a stubbed JSON body, an assertion on the parsed result, and a separate test for the error path.

## What to implement

1. **`get_digest_ready() -> bool`** issues `GET /api/v1/digest/ready` with the resolved auth token, expects `{"ready": bool}`, and returns the boolean. `401` raises `ApiClientError("auth_required")`; `429` raises `ApiClientError("rate_limited", retry_after=...)`; any other non-200 raises `ApiClientError("http_error", status=..., body=...)`.
2. **`get_digest_text() -> str`** issues `GET /api/v1/digest`, formats the returned list into a single sentence `"You have N reviews due today: <topic 1>, <topic 2>, and <topic M>."`, returns the string. Empty list returns `"You have no reviews due today."`.
3. **`start_voice_quiz_attempt(quiz_set_id, *, auth_token=None) -> dict`** issues `POST /api/v1/quiz/{quiz_set_id}/attempts {"mode": "voice"}`, returns the response dict `{attempt_id, question_text, options}`. `400` with `"Voice mode is not yet supported"` (the Phase 4 leftover) raises `ApiClientError("server_phase5_pending")` so the desktop fails fast if it is pointed at a pre-Phase-5 server.
4. **`send_voice_turn(attempt_id, answer_text, *, auth_token=None) -> dict`** issues `POST /api/v1/quiz/attempts/{attempt_id}/voice-turn {"answer_text": answer_text}`, returns the `VoiceTurnResponse` dict. `409` ("Attempt already completed") raises `ApiClientError("attempt_completed")`.
5. **Auth resolution** matches the existing methods: explicit `auth_token` parameter wins, otherwise `load_token()` is called once per request. `start_voice_quiz_attempt` and `send_voice_turn` always require a token; `get_digest_ready` and `get_digest_text` raise `ApiClientError("auth_required")` when the server returns `401`.
6. **Resource cleanup** wraps every `httpx.Client` in a `try/finally` that closes the client. Long-running desktop processes must not leak sockets.

## Where to look

- [`docs/design/technical-design.md`](../docs/design/technical-design.md) §3.29 for the canonical method signatures.
- [`src/sra/client/api_client.py`](../src/sra/client/api_client.py) for the existing patterns (single `_request` helper, `auth_token` resolution, `ApiClientError` mapping, `try/finally` cleanup).
- [`src/sra/api/routers/digest.py`](../src/sra/api/routers/digest.py) (SRA-502) for the response shape of `/digest/ready`.
- [`src/sra/api/routers/quiz.py`](../src/sra/api/routers/quiz.py) (SRA-505) for the response shape of the voice routes.

## Acceptance criteria

- [x] Each of the four methods issues exactly one HTTP request, parses it strictly, and returns the documented shape
- [x] Missing/invalid auth raises `ApiClientError("auth_required")`; rate-limited responses raise `ApiClientError("rate_limited")` with `Retry-After` exposed
- [x] `get_digest_text()` produces a human-readable sentence from the `ReviewOut[]` payload and handles the empty-digest case
- [x] `send_voice_turn()` maps `409` to `ApiClientError("attempt_completed")`
- [x] `start_voice_quiz_attempt()` maps the Phase 4 `400` "Voice mode is not yet supported" to `ApiClientError("server_phase5_pending")`
- [x] No socket leaks under repeated calls (covered by a `gc.collect()` / open-file-count test)
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_api_client.py -v
pytest tests/e2e/test_phase5_api_client_e2e.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

Expected: every focused + e2e test passes; `-m 'not phase5' -q` keeps Phase 1/2/3/4 green; Ruff and Pyright are clean.

## Manual verification

```bash
# Run the existing Phase 2 client tests' loopback fixture pattern against a real server
TOKEN=$(...)
python -c "
from sra.client.api_client import ApiClient, ApiClientError
client = ApiClient(server_base_url='http://localhost:8000', auth_token='$TOKEN')
print(client.get_digest_ready())
print(client.get_digest_text())
print(client.start_voice_quiz_attempt(quiz_set_id=1))
"
```

Expected: prints `True`/`False`, the digest sentence, and the voice attempt dict. (Manual only; the unit tests cover the same paths with the transport mocked.)

## Verification record

- **Date and environment:** 2026-10-14, local macOS, Python 3.13, existing `.venv`.
- **Commands and inputs:**
  - `pytest tests/unit/test_api_client.py tests/e2e/test_phase5_api_client_e2e.py -v`
  - `pytest -m 'not phase5' -q`
  - `ruff check .`
  - `pyright`
- **New IDs/artifacts and observed downstream results:**
  - `src/sra/client/api_client.py`: added `_extract_detail`, `_auth_required_error`, `_rate_limited_error`, `_http_error`, `_authed_get`, `_authed_post` helpers plus the four public methods `get_digest_ready`, `get_digest_text`, `start_voice_quiz_attempt`, `send_voice_turn`. `ApiClientError` gains a class-level `retry_after: int | None = None` annotation so `pyright` accepts the assignment in `_rate_limited_error`.
  - No new artifacts; no schema or migration changes.
- **Failure/recovery and cleanup:**
  - Initial run failed pyright with `Cannot assign to attribute "retry_after"`. Fixed by declaring `retry_after: int | None = None` on `ApiClientError` (the existing pattern of declaring extra state on `__init__` parameters, only as a class-level annotation to keep the constructor's call sites stable). No test, behaviour, or public-API changes were needed.
- **Automated/manual results, skips, and limitations:**
  - Focused phase 5 tests: **75 passed** in `tests/unit/test_api_client.py` + `tests/e2e/test_phase5_api_client_e2e.py`.
  - `pytest -m 'not phase5' -q`: **479 passed, 13 pre-existing Phase 3/4 failures, 14 skipped** — matches the SRA-505 baseline (no new regressions).
  - `ruff check .`: all checks passed.
  - `pyright`: 0 errors, 0 warnings.
  - Manual smoke against a real server is not part of this ticket — SRA-507 and SRA-508 exercise the new methods end-to-end.

## Definition of Done

- [x] All acceptance criteria met and actual evidence recorded
- [x] Full focused + e2e suites pass
- [x] Phase 1/2/3/4 regressions remain green; Ruff and Pyright pass
- [x] `tickets/README.md` board and `PROGRESS.md` updated with the new ticket's completion