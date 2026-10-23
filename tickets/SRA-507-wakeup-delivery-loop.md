# SRA-507 — Wake-up delivery loop

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Add `src/sra/capture/wakeup.py` with the desktop-side loop that turns the user's
`preferred_review_time` into audible speech. Three public functions:
`poll_digest_ready(api_client) -> bool` (thin wrapper over the client's
`get_digest_ready()`), `deliver_digest(api_client) -> None` (fetches the digest
sentence and calls `tts.speak(...)`), and
`run_wakeup_loop(stop_event, *, poll_interval_s=60) -> None` (a long-running
thread that polls and delivers until `stop_event` is set). This is the client half
of the wake-up flow; the server half (scheduler, `digest/ready`,
`wakeup_notification_sent_at`) is SRA-502. The loop owns no state beyond the
`stop_event` and the poll cadence, so it is testable without a server and the
server is testable without spawning a thread.

## Unlocks

- A developer can run `run_wakeup_loop(stop, api_client=ApiClient(...), poll_interval_s=10)` on their Mac and observe the loop polling every 10 seconds, speaking the digest sentence when `ready=True`, and shutting down cleanly when `stop.set()` is called.
- The eventual menu-bar integration is a one-line `run_wakeup_loop(stop_event, ...)` call inside the existing `rumps` app; SRA-507 ships the loop as a standalone importable function with no UI dependency.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/capture/wakeup.py`](../src/sra/capture/wakeup.py) | new | `poll_digest_ready(api_client) -> bool`, `deliver_digest(api_client) -> None`, `run_wakeup_loop(stop_event, *, api_client=None, poll_interval_s=60) -> None`, plus a `DigestClient` Protocol describing `get_digest_ready() -> bool` and `get_digest_text() -> str`. `run_wakeup_loop` builds an `ApiClient` from `Settings.server_base_url` when `api_client` is `None`. |
| [`src/sra/capture/__init__.py`](../src/sra/capture/__init__.py) | edit | Re-export `run_wakeup_loop`, `poll_digest_ready`, `deliver_digest`. |
| [`tests/unit/test_wakeup.py`](../tests/unit/test_wakeup.py) | new | Unit tests with a stub `api_client` and a recording `tts` backend: poll-returns-true → speak called once; poll-returns-false → no speak; `stop_event.set()` breaks the loop promptly; an API exception is caught and logged and the loop continues. |

## Acceptance criteria

- [x] `poll_digest_ready(api_client)` returns the underlying `get_digest_ready()` value
- [x] `deliver_digest(api_client)` calls `get_digest_text()` then `tts.speak(text)` exactly once each, in that order
- [x] `run_wakeup_loop` polls, speaks when `ready=True`, sleeps when `ready=False`, and exits within `poll_interval_s` of `stop_event.set()`
- [x] Network/audio exceptions inside the loop body do not kill the loop; they are logged and the next iteration runs
- [x] No audio output during pause/dialog (verified by the recording-tts-backend test asserting zero `speak` calls when `ready=False`)
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_wakeup.py -v
pytest tests/e2e/test_phase5_wakeup_e2e.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

The sleep is `stop_event.wait(poll_interval_s)`, not `time.sleep(poll_interval_s)`, so the
loop shuts down promptly without burning CPU on a separate stop check. Any
exception in the loop body is logged at WARNING and swallowed so the loop
survives transient network or audio failures; only `KeyboardInterrupt`
propagates, so the menu-bar app can quit on Ctrl-C.

## Manual verification

- [ ] With a recording TTS backend and a running server, `run_wakeup_loop` prints at least one `[say] You have N reviews due today: ...` line (assuming due reviews exist), then exits promptly after `stop.set()`.

**Verification:**

- **Date/environment:** 2026-10-14, macOS Darwin, Python 3.13.12 from `.venv`, repo `/Users/shubhamsingh/Documents/cells/spaced-repetition-agent`.
- Focused `pytest tests/unit/test_wakeup.py tests/e2e/test_phase5_wakeup_e2e.py -v` → **11 passed** (9 unit + 2 e2e).
- `pytest -m 'not phase5' -q` → **479 passed, 13 pre-existing Phase 3/4 failures, 14 skipped** — identical to the SRA-506 baseline.
- `ruff check .` → **All checks passed.** `pyright` → **0 errors, 0 warnings, 0 informations.**
- New artifacts: `src/sra/capture/wakeup.py` exposing `poll_digest_ready`, `deliver_digest`, and `run_wakeup_loop` with a `DigestClient` Protocol; the three names re-exported from `src/sra/capture/__init__.py`.
- Failure/recovery: Pyright first rejected the `_DigestClient` class annotation when the `api_client=None` branch assigned the module to the typed client variable — replaced with a `Protocol`. A second failure, `test_run_wakeup_loop_survives_a_per_iteration_exception`, inspected `record.getMessage()` while `exc_info=True` only attaches the traceback to `record.exc_info`; fixed by interpolating the exception (`"%s"`) into the formatted message while still passing `exc_info=True`. Suite re-run clean.
- Limitation: manual `say` audio acceptance rolls into the SRA-509 desktop verification, per SRA-503's verification record.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
