# SH-006 — Empty dashboard leaves upcoming count unresolved

**Type:** Bug
**Priority:** Medium
**Status:** Fixed
**Affected feature:** Study dashboard (`/dashboard`)
**Baseline revision:** `754274b`

## User impact

A signed-in user with no upcoming reviews sees the upcoming section finish with
the correct empty-state message, while the summary tile above it remains `—`.
The mixed state makes the dashboard look partially loaded or broken.

## Preconditions and reproduction

1. Start the application with an empty database and sign in.
2. Open `/dashboard` in a browser.
3. Wait for the entries, digest, and upcoming-review requests to finish.

**Expected:** The “Upcoming (next 30 days)” tile shows `0`, consistent with the
empty upcoming-review response and message.

**Actual:** The tile continues to show `—` after the section says “No upcoming
reviews in this window.”

**Evidence:** Reproduced on 2026-10-24 with Playwright MCP against a local
Uvicorn server and a fresh SQLite database. `GET /api/v1/reviews/upcoming?days=30`
returned an empty list; the rendered `#stat-upcoming-total` text remained `—`.

## Root cause

`renderUpcomingEmpty()` rendered the empty panel and status text but never
resolved `#stat-upcoming-total`. Only the non-empty branch assigned a count.

## Acceptance criteria

- An empty upcoming-review response renders `0` in the summary tile.
- The existing empty-state copy remains visible.
- The real-browser dashboard startup test asserts the resolved count.
- Non-empty review groups continue to render their calculated total.

## Regression test

`tests/playwright/test_user_flows.py::test_dashboard_starts_without_page_or_console_errors`

## Verification record

- Playwright MCP, 2026-10-24: reproduced before the fix on an empty account.
- Playwright MCP loaded the corrected module and observed
  `#stat-upcoming-total` resolve to `0`.
- Full real-Chromium suite: 22 passed.
