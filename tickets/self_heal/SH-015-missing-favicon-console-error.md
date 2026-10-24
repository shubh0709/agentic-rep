# SH-015 — Every first page load logs a favicon 404

**Type:** Bug
**Priority:** Low
**Status:** Fixed
**Affected feature:** Shared web page shell
**Baseline revision:** `754274b`

## User impact

Every fresh browser session requests `/favicon.ico`, which the application does
not serve. The resulting 404 appears as an error in the browser console and in
resource telemetry, adding noise that can obscure meaningful page failures.

## Preconditions and reproduction

1. Open the public landing page in a fresh browser context.
2. Inspect error-level console messages and network requests.

**Expected:** Shared page assets load without errors.

**Actual:** `/favicon.ico` returns 404 and logs “Failed to load resource”.

**Evidence:** Reproduced on the first Playwright MCP navigation on 2026-10-24.

## Root cause

The shared base template declared no icon, so Chromium made its conventional
`/favicon.ico` fallback request even though that path was not mounted.

## Acceptance criteria

- The base template points to a mounted favicon asset.
- Anonymous landing-page navigation has no page or error-level console events.
- The icon request returns successfully through the existing static mount.

## Regression test

`tests/playwright/test_user_flows.py::test_landing_page_renders_without_session`

## Verification record

- Playwright MCP, 2026-10-24: reproduced before the fix as a 404 console error.
- Final full real-Chromium suite: 22 passed, including the clean-console
  anonymous landing smoke test.
