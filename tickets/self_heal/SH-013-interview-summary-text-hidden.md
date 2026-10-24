# SH-013 — Completed interview hides its summary text

**Type:** Bug
**Priority:** High
**Status:** Fixed
**Affected feature:** Browser interview completion summary
**Baseline revision:** `754274b`
**Dependency:** SH-011

## User impact

After a browser interview finishes, the page replaces the conversation with an
“Interview summary” panel but omits the generated summary text. Users lose the
feedback that explains the outcome of their interview.

## Preconditions and reproduction

1. Start an interview from the dashboard.
2. Submit an answer that finishes the attempt.
3. Inspect the completion panel.

**Expected:** The generated summary returned by the turn endpoint is visible.

**Actual:** The heading and back link render, but the summary text is absent.

**Evidence:** The full Chromium Playwright suite on 2026-10-24 completed the
real browser → API → database turn and found the summary panel, but could not
find the server’s `"Browser interview completed."` text.

## Root cause

`InterviewTurnResponse.summary` is `str | None`, while `renderSummary()` only
read object fields such as `summary.summary_text`. The string was truthy, so it
also bypassed the “No summary available” fallback and rendered nothing.

## Acceptance criteria

- String summaries returned by an interview turn render visibly.
- Object-shaped summaries returned by the resume/detail endpoint continue to
  render score, counts, and `summary_text`.
- Unit and real-browser tests assert the exact completion summary.

## Regression tests

- `tests/unit/test_interview_dashboard.py::test_render_after_finish_shows_summary_panel`
- `tests/playwright/test_user_flows.py::test_interview_dashboard_send_button_completes_browser_turn`

## Verification record

- Full Chromium Playwright suite, 2026-10-24: reproduced as the only failure
  after SH-006 through SH-012.
- Focused unit tests: 7 passed.
- Focused real-Chromium regression: 1 passed.
- Full real-Chromium suite: 22 passed.
