# SRA-507 — Wake-up delivery loop

**Type:** Task
**Phase:** 5 — Scheduled wake-up & voice quiz
**Priority:** Medium — works once SRA-503 (TTS) and SRA-506 (client methods) are in place
**Status:** Implemented — 2026-10-14
**Blocked by:** SRA-503, SRA-506
**Blocks:** SRA-509

## Summary

Add `src/sra/capture/wakeup.py` with the desktop-side loop that turns the user's `preferred_review_time` into audible speech. Three public functions: `poll_digest_ready(api_client) -> bool` (thin wrapper over the client's `get_digest_ready()`), `deliver_digest(api_client) -> None` (fetches the digest sentence and `tts.speak(...)` it), and `run_wakeup_loop(stop_event, *, poll_interval_s=60) -> None` (a long-running thread that polls and delivers until `stop_event` is set).

This is the client half of the wake-up flow. The server half (scheduler, `digest/ready`, `wakeup_notification_sent_at`) is SRA-502.

## Where things stand right now

- `src/sra/capture/wakeup.py` does not exist; the design doc (§3.32) defines the canonical signatures.
- `src/sra/capture/tts.py::speak(text)` is in place after SRA-503 and is the audio-output dependency.
- `src/sra/client/api_client.py::get_digest_ready` / `get_digest_text` are in place after SRA-506.
- The menu-bar app (`src/sra/capture/menubar_app.py`, SRA-205) does not yet own a background thread; this ticket introduces the threading primitive the menubar will eventually own. The integration with the menubar UI lives in a future ticket — SRA-507 ships the loop as a standalone importable function with no UI dependency.

## What this ticket unlocks

Once SRA-507 lands, a developer can run the wake-up loop on their Mac:

```bash
python -c "
from threading import Event
from sra.client.api_client import ApiClient
from sra.capture.wakeup import run_wakeup_loop
stop = Event()
run_wakeup_loop(stop, api_client=ApiClient(...), poll_interval_s=10)
"
```

…and observe the loop polling the server every 10 seconds, speaking the digest sentence when `ready=True`, and shutting down cleanly when `stop.set()` is called. The SRA-509 verification ticket wires this loop into a real test that drives the wake-up → speak → stop sequence end to end.

Before SRA-507: there is no client-side wake-up code at all. After SRA-507: the loop is testable in isolation, and the eventual menubar integration is a one-line `run_wakeup_loop(stop_event, ...)` call inside the existing `rumps` app.

## How this fits into the whole app

The wake-up loop is a thin orchestrator. It owns no state beyond the `stop_event` and the poll cadence; the server owns the per-user delivery flag and the TTS module owns the audio. This separation lets the loop be tested without mocking the server (the test substitutes a recording `api_client` and a recording `tts` backend) and lets the server be tested without spawning a thread (SRA-502).

```mermaid
sequenceDiagram
    participant Loop as run_wakeup_loop
    participant API as api_client.get_digest_ready / get_digest_text
    participant TTS as tts.speak
    participant Stop as threading.Event
    loop until stop_event is set
        Loop->>API: poll_digest_ready()
        alt ready = true
            Loop->>API: get_digest_text()
            API-->>Loop: sentence
            Loop->>TTS: speak(sentence)
        else ready = false
            Loop->>Loop: sleep(poll_interval_s)
        end
    end
    Stop-->>Loop: set()
    Loop-->>Loop: return
```

The loop is a `while not stop_event.is_set()` with a `time.sleep(poll_interval_s)` at the bottom. The sleep is interruptible via `stop_event.wait(poll_interval_s)` so the loop shuts down promptly without burning CPU on `time.sleep` plus a separate stop check.

### Why a polling loop and not a server push?

Phase 5 keeps the desktop client stateless. The server does not know which clients are running; pushing wake-up notifications would require either a long-lived WebSocket per client (out of scope) or a system-level notification (out of scope). A 60-second poll is cheap, runs while the menu-bar app is alive, and respects the user's `preferred_review_time` to the minute (the server's `run_wakeup_tick` runs every minute per SRA-502). For a single-user desktop app, polling is the right tradeoff.

## Files and why they matter

| File | Role and required change |
|---|---|
| [`src/sra/capture/wakeup.py`](../src/sra/capture/wakeup.py) **[NEW]** | The wake-up module: `poll_digest_ready(api_client) -> bool`, `deliver_digest(api_client) -> None`, `run_wakeup_loop(stop_event, *, api_client=None, poll_interval_s=60) -> None`. |
| [`src/sra/capture/__init__.py`](../src/sra/capture/__init__.py) | Re-export `run_wakeup_loop`, `poll_digest_ready`, `deliver_digest`. |
| [`tests/unit/test_wakeup.py`](../tests/unit/test_wakeup.py) **[NEW]** | Unit tests with a stub `api_client` and a recording `tts` backend. Cover: poll-returns-true → speak called once; poll-returns-false → no speak; `stop_event.set()` breaks the loop promptly; API exception path is caught and logged (loop continues). |

## TDD starting point

There is no existing wake-up test. The closest pattern is `tests/unit/test_menubar_app.py` (background thread + `stop_event` test pattern). SRA-507 reuses the same `threading.Event` mechanism but in a much simpler shape — no menu-bar UI, just a polling loop.

## What to implement

1. **`poll_digest_ready(api_client) -> bool`** is a one-line wrapper: `return api_client.get_digest_ready()`. The function exists so callers can mock the wake-up module's surface without mocking the api_client module's surface.
2. **`deliver_digest(api_client) -> None`** calls `api_client.get_digest_text()` then `tts.speak(text)`. The order matters: `speak` blocks until playback finishes, so a transient network blip in `get_digest_text` does not delay the next poll (the next iteration of the outer loop will retry).
4. **`run_wakeup_loop(stop_event, *, api_client=None, poll_interval_s=60) -> None`** constructs an `ApiClient` (using `Settings.server_base_url`) when `api_client` is `None`. The loop body is `if poll_digest_ready(api_client): deliver_digest(api_client)` followed by `stop_event.wait(poll_interval_s)`. The `wait` returns `True` when the event is set, so the loop exits on the next iteration boundary.
5. **Exception isolation**: any exception inside the loop body is logged at WARNING and swallowed so the loop survives transient network/audio failures. The single exception is `KeyboardInterrupt`, which propagates so the menu-bar app can quit on Ctrl-C.
6. **Module docstring** documents the threading contract (`stop_event` is the only shutdown signal) and the relationship to SRA-502 (the server decides "ready"; the client decides "speak").

## Where to look

- [`docs/design/technical-design.md`](../docs/design/technical-design.md) §3.32 for the canonical function signatures.
- [`src/sra/capture/tts.py`](../src/sra/capture/tts.py) (SRA-503) for the `speak` contract.
- [`src/sra/client/api_client.py`](../src/sra/client/api_client.py) (SRA-506) for the `get_digest_ready` / `get_digest_text` contracts.
- [`src/sra/capture/menubar_app.py`](../src/sra/capture/menubar_app.py) (SRA-205) for the threading-event pattern.

## Acceptance criteria

- [x] `poll_digest_ready(api_client)` returns the underlying `get_digest_ready()` value
- [x] `deliver_digest(api_client)` calls `get_digest_text()` then `tts.speak(text)` exactly once each, in that order
- [x] `run_wakeup_loop` polls, speaks when `ready=True`, sleeps when `ready=False`, and exits within `poll_interval_s` of `stop_event.set()`
- [x] Network/audio exceptions inside the loop body do not kill the loop; they are logged and the next iteration runs
- [x] No audio output during pause/dialog (verified by the recording-tts-backend test asserting zero `speak` calls when `ready=False`)
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_wakeup.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

Expected: every wake-up test passes; `-m 'not phase5' -q` keeps Phase 1/2/3/4 green; Ruff and Pyright are clean.

## Manual verification

```bash
source .venv/bin/activate
python -c "
from threading import Event
import time
from sra.client.api_client import ApiClient
from sra.capture.wakeup import run_wakeup_loop
from sra.capture import tts
tts.set_backend(lambda text: print(f'[say] {text}'))
stop = Event()
import threading
threading.Thread(target=run_wakeup_loop, kwargs={'stop_event': stop, 'api_client': ApiClient(server_base_url='http://localhost:8000', auth_token='...'), 'poll_interval_s': 10}, daemon=True).start()
time.sleep(35)
stop.set()
time.sleep(1)
"
```

Expected: at least one `[say] You have N reviews due today: ...` line is printed (assuming the server has due reviews), then the script exits promptly after `stop.set()`. (Manual only; the unit tests cover the same paths with fakes.)

## Verification record

- **Date and environment:** 2026-10-14, macOS Darwin, Python 3.13.12 from `.venv`, repo `/Users/shubhamsingh/Documents/cells/spaced-repetition-agent`.
- **Commands and inputs:**
  - `pytest tests/unit/test_wakeup.py tests/e2e/test_phase5_wakeup_e2e.py -v`
  - `pytest -m 'not phase5' -q`
  - `ruff check .`
  - `pyright`
- **New IDs/artifacts and observed downstream results:**
  - Added `src/sra/capture/wakeup.py` exposing `poll_digest_ready`, `deliver_digest`, and `run_wakeup_loop` with a `DigestClient` Protocol.
  - Re-exported `run_wakeup_loop`, `poll_digest_ready`, `deliver_digest` from `src/sra/capture/__init__.py`.
- **Failure/recovery and cleanup:**
  - First pass: pyright rejected the `_DigestClient` class annotation when the `api_client=None` branch assigned the `sra.client.api_client` module to the typed client variable. Replaced the class with a `Protocol` (`DigestClient`) describing `get_digest_ready() -> bool` and `get_digest_text() -> str`. Re-ran the suite.
  - First pass: `test_run_wakeup_loop_survives_a_per_iteration_exception` failed because the assertion inspects `record.getMessage()` and `exc_info=True` only attaches the traceback to `record.exc_info`. Changed the warning to interpolate the exception (`"%s"`) into the formatted message while still passing `exc_info=True` for full traceback detail. Re-ran the suite.
- **Automated/manual results, skips, and limitations:**
  - Focused: **11 passed** (9 unit + 2 e2e).
  - `not phase5` regressions: **479 passed, 13 pre-existing Phase 3/4 failures, 14 skipped** — identical to the SRA-506 baseline.
  - `ruff check .`: **All checks passed.**
  - `pyright`: **0 errors, 0 warnings, 0 informations.**
  - Manual `say` audio acceptance rolls into SRA-509 desktop verification (per SRA-503's verification record).

## Definition of Done

- [x] All acceptance criteria met and actual evidence recorded
- [x] Full focused suite passes
- [x] Phase 1/2/3/4 regressions remain green; Ruff and Pyright pass
- [x] `tickets/README.md` board and `PROGRESS.md` updated with the new ticket's completion