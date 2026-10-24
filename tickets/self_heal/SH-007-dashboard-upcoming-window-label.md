# SH-007 — Upcoming summary label ignores selected window

**Type:** Bug
**Priority:** Medium
**Status:** Fixed
**Affected feature:** Study dashboard upcoming-review filter
**Baseline revision:** `754274b`
**Dependency:** SH-006

## User impact

Changing the dashboard’s review window to 7, 14, or 90 days refreshes the
review list and count, but the summary tile continues to say “Upcoming (next
30 days).” The displayed count is therefore mislabeled and can lead users to
misread their workload.

## Preconditions and reproduction

1. Sign in and open `/dashboard`.
2. Change “Days ahead” from “Next 30 days” to “Next 7 days”.
3. Wait for the upcoming-review request to finish.

**Expected:** The summary tile says “Upcoming (next 7 days)” and its count
represents the same window.

**Actual:** The list and count use 7 days, while the tile still says “next 30
days.”

**Evidence:** Reproduced with Playwright MCP on 2026-10-24. The selected
`#upcoming-window` value was `7`; the accessible snapshot still exposed
“Upcoming (next 30 days).”

## Root cause

The tile label was static template text. `renderUpcoming(days)` refreshed the
request, status, and count but did not update the label.

## Acceptance criteria

- Selecting 7, 14, 30, or 90 days updates the summary label to the same value.
- The review request and count continue to use the selected window.
- A real-browser regression test exercises the dropdown and visible label.

## Regression test

`tests/playwright/test_user_flows.py::test_dashboard_upcoming_summary_tracks_selected_window`

## Verification record

- Playwright MCP, 2026-10-24: reproduced before the fix with the 7-day option.
- Full real-Chromium suite: 22 passed, including the 7-day label assertion.
