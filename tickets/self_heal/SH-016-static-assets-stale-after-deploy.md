# SH-016 — Existing browsers reuse stale assets after deploy

**Type:** Bug
**Priority:** High
**Status:** Fixed
**Affected feature:** Shared web assets and browser page modules
**Baseline revision:** `754274b`

## User impact

After the server deploys updated JavaScript or CSS, an already-open browser can
reuse the old unversioned assets on its next page navigation. The HTML is
current but behavior and layout remain on the previous release until cache
expiry or a hard refresh.

## Preconditions and reproduction

1. Load `/dashboard` in a browser.
2. Update the dashboard JavaScript and CSS, then restart the server.
3. Navigate to `/dashboard` again in the same browser context.

**Expected:** The page loads the assets shipped with the current server.

**Actual:** The updated template rendered, but the browser reused old
`/static/js/dashboard.js` and `/static/css/site.css`. Playwright MCP again
measured `scrollWidth=491` at a 375 px viewport and saw the stale `—` count,
even though both source fixes were present on disk.

## Root cause

All static asset URLs were stable across releases. A normal browser cache could
therefore consider an older response reusable after a deployment.

## Acceptance criteria

- Shared CSS, authentication JavaScript, page modules, favicon, and interview
  module use a release-version query parameter.
- The current release's templates all reference the same static version.
- A browser regression asserts public landing assets carry a version.
- Repeating the MCP smoke in the existing browser loads the repaired layout and
  dashboard state.

## Regression test

`tests/playwright/test_user_flows.py::test_landing_page_renders_without_session`

## Verification record

- Playwright MCP, 2026-10-24: reproduced after a server restart in the same
  browser context.
- Existing-browser Playwright MCP smoke loaded all assets with `?v=20261024`,
  measured `scrollWidth=360` at a 375 px viewport, kept Sign out visible, and
  resolved the upcoming count to `0`.
- Final real-Chromium suite: 22 passed.
