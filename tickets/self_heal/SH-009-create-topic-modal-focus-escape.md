# SH-009 — Create-topic modal lets keyboard focus escape

**Type:** Bug
**Priority:** High
**Status:** Fixed
**Affected feature:** Dashboard “Create topic” dialog
**Baseline revision:** `754274b`

## User impact

Keyboard-only users can tab out of the open modal into controls hidden behind
its backdrop. Focus becomes detached from the visible task, which makes the
dialog confusing and difficult to complete or close.

## Preconditions and reproduction

1. Sign in, open `/dashboard`, and activate “Create topic”.
2. Put focus on the dialog’s Close button.
3. Press Shift+Tab.

**Expected:** Focus wraps to the last enabled control in the modal (“Generate
now”).

**Actual:** Focus moves to the background “Days ahead” select element.

**Evidence:** Reproduced with Playwright MCP on 2026-10-24 by reading
`document.activeElement` after keyboard navigation.

## Root cause

The modal keydown handler supported Escape only. It did not intercept Tab at
the first or last focusable element, so the browser continued through the
document’s normal focus order behind the modal backdrop.

## Acceptance criteria

- Shift+Tab from the first modal control wraps to the last modal control.
- Tab from the last modal control wraps to the first modal control.
- Escape still closes the modal and restores focus to “Create topic”.
- A real-browser regression test proves focus remains inside the dialog.

## Regression test

`tests/playwright/test_user_flows.py::test_create_topic_modal_traps_keyboard_focus`

## Verification record

- Playwright MCP, 2026-10-24: reproduced before the fix; focus escaped to
  `#upcoming-window`.
- Full real-Chromium suite: 22 passed; Shift+Tab now wraps to Generate now.
