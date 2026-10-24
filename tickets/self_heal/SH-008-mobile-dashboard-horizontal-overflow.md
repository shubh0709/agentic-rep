# SH-008 — Mobile dashboard overflows horizontally

**Type:** Bug
**Priority:** High
**Status:** Fixed
**Affected feature:** Authenticated web navigation and study dashboard
**Baseline revision:** `754274b`

## User impact

On a phone-sized viewport, the authenticated navigation remains a single row.
It pushes the page wider than the screen, clips the Sign out control, and
forces users to scroll sideways to reach navigation and dashboard content.

## Preconditions and reproduction

1. Sign in and open `/dashboard`.
2. Set the viewport to 375 × 667 CSS pixels.
3. Inspect the page width and navigation controls.

**Expected:** The page fits the viewport, navigation wraps within the header,
and Sign out remains fully visible.

**Actual:** `window.innerWidth` was 375 while
`document.documentElement.scrollWidth` was 491. The navigation width was about
387 pixels before accounting for the brand, and Sign out rendered past the
right edge.

**Evidence:** Reproduced and measured with Playwright MCP on 2026-10-24; the
full-page screenshot also showed the clipped header.

## Root cause

The shared stylesheet had no narrow-viewport rules. `.site-header` and
`.site-nav` were non-wrapping flex rows, so the authenticated link set imposed
a minimum page width larger than common phones.

## Acceptance criteria

- At 375 CSS pixels, the document has no horizontal overflow.
- Authenticated navigation wraps and Sign out is fully inside the viewport.
- Dashboard search and window controls shrink to the available content width.
- Desktop layout remains unchanged above the mobile breakpoint.

## Regression test

`tests/playwright/test_user_flows.py::test_dashboard_fits_mobile_viewport_without_horizontal_scroll`

## Verification record

- Playwright MCP, 2026-10-24: reproduced at 375 × 667 (`scrollWidth=491`).
- Full real-Chromium suite: 22 passed, including the 375 × 667 geometry check.
