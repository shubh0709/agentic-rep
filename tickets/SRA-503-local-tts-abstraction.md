# SRA-503 — Local TTS abstraction

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Add `src/sra/capture/tts.py` with `speak(text: str) -> None`, the project's only audio-output component. The default backend invokes macOS `say` through `subprocess.run`; `set_backend(callable)` substitutes a recorder in tests, and `reset_backend()` restores the default. `speak()` blocks until playback finishes so callers know when listening may resume. Empty text is a no-op, and importing the module never invokes `say`.

## Unlocks

- SRA-507 wake-up delivery and SRA-508 voice quiz can speak digest or question text through one stable, testable desktop boundary.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/capture/tts.py`](../src/sra/capture/tts.py) | new | Defines `Backend`, `_say_backend`, `_backend`, `speak(text)`, `set_backend(callable)`, and `reset_backend()`. |
| [`src/sra/capture/__init__.py`](../src/sra/capture/__init__.py) | edit | Re-exports `speak`, `set_backend`, and `reset_backend`. |
| [`tests/unit/test_tts.py`](../tests/unit/test_tts.py) | new | Verifies forwarding, empty text, backend reset, subprocess shape, failures, and import safety. |

## Acceptance criteria

- [x] `speak(text)` invokes the configured backend exactly once with the full text
- [x] The default backend invokes `subprocess.run(["say", text], check=True)`; `say` failing raises `CalledProcessError`
- [x] `speak("")` is a no-op (does not invoke the backend)
- [x] `set_backend(callable)` swaps the backend for the rest of the process; `reset_backend()` restores `say`
- [x] The module is import-safe on non-macOS platforms (no `say` lookup at import time; only at first call)
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_tts.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

The focused tests must not invoke real audio. The phase regression retains the documented Phase 4 baseline.

## Manual verification

- [ ] On macOS, run `python -c "from sra.capture.tts import speak; speak('Spaced repetition agent wake up test.')"` and confirm audible, complete playback.

**Verification:**
- **2026-10-14, macOS, Python 3.13.12:** `pytest tests/unit/test_tts.py -v` reported **11 passed**; `ruff check .` passed; `pyright` reported **0 errors, 0 warnings, 0 informations**.
- `pytest -m 'not phase5' -q` recorded **482 passed, 13 pre-existing Phase 3/4 failures, 14 skipped, 188 deselected**, matching the SRA-502 baseline and introducing no regression.
- New `src/sra/capture/tts.py` uses `Backend = Callable[[str], None]`, `_backend: Backend = _say_backend`, `subprocess.run(["say", text], check=True)`, and an empty-text short circuit. `src/sra/capture/__init__.py` re-exports the three public functions.
- Tests covered verbatim forwarding, empty text, the exact subprocess call, `CalledProcessError` propagation, backend swap/reset, and import-time safety on non-macOS. The autouse reset fixture and explicit resets restored the default after each test.
- The 13 failures remained the documented Phase 3/4 baseline. No manual `speak()` audio check ran; real playback remains part of SRA-509 acceptance.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
