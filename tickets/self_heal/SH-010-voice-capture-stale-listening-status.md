# SH-010 — Voice capture stays “Listening” after stop

**Type:** Bug
**Priority:** Medium
**Status:** Fixed
**Affected feature:** Landing-page browser voice capture
**Baseline revision:** `754274b`

## User impact

After stopping browser dictation, the Record button correctly returns to its
idle label but the live status still says “Listening. Click Record again to
stop.” Users cannot tell whether the microphone is still active and receive
contradictory state from adjacent controls.

## Preconditions and reproduction

1. Sign in on a browser that exposes the Web Speech API.
2. Click Record, then click Stop recording.
3. Wait for the recognizer’s `end` event.

**Expected:** The button and live status both report that recording stopped.

**Actual:** The button says “Record” with `aria-pressed=false`, while the live
status continues to say “Listening” indefinitely.

**Evidence:** Reproduced with Playwright MCP Chromium on 2026-10-24 and still
present two seconds after the stop action.

## Root cause

The recognizer `onend` handler reset the internal flag, button state, and label,
but did not update the status region. Error and submit paths had their own
status updates, so the stale success-path text remained untouched.

## Acceptance criteria

- A normal recognizer end replaces the Listening status with a stopped message.
- The button returns to “Record” and `aria-pressed=false`.
- Error messages and submit/clear status updates are not overwritten.
- A deterministic Playwright regression test covers the start/stop lifecycle.

## Regression test

`tests/playwright/test_user_flows.py::test_landing_voice_capture_reports_stopped_state`

## Verification record

- Playwright MCP, 2026-10-24: reproduced before the fix in Chromium.
- Full real-Chromium suite: 22 passed with a deterministic speech-recognition
  start/end cycle.
