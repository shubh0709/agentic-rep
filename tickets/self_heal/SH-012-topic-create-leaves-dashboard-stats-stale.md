# SH-012 — Topic creation leaves dashboard summaries stale

**Type:** Bug
**Priority:** High
**Status:** Fixed
**Affected feature:** Dashboard topic authoring and review summaries
**Baseline revision:** `754274b`
**Dependency:** SH-006

## User impact

Creating a topic inserts its card into the entry list, but the “Total entries,”
“Due today,” and “Upcoming” summaries keep their pre-create values. The server
has already scheduled the topic’s reviews, yet the same dashboard says there
are none until the user reloads the page.

## Preconditions and reproduction

1. Sign in with an empty account and open `/dashboard`.
2. Create a topic with a pasted-text reference.
3. Wait for the new topic card to appear.

**Expected:** Total entries increases to 1 and upcoming reviews refresh to show
the newly scheduled checkpoints.

**Actual:** The card appears, while Total entries and Upcoming keep their old
values.

**Evidence:** The browser create-success path called only
`prependEntryCard()`. That function updated the entries status sentence but
not the total tile, and no due/upcoming reload was scheduled. The existing
Playwright topic flow did not assert the summary values.

## Root cause

The topic modal was added after the dashboard’s initial data loaders. Its
success handler performed a local list insertion without reconnecting the new
entry to the three summary loaders.

## Acceptance criteria

- Successful topic creation increments Total entries without a full reload.
- Due-today and upcoming-review summaries re-read their API data.
- The upcoming label continues to match the selected window.
- The real-browser topic flow asserts both the entry and review counts.

## Regression test

`tests/playwright/test_user_flows.py::test_create_topic_with_paste_renders_valid_quiz_and_interview_actions`

## Verification record

- Static call-path inspection and Playwright coverage gap confirmed on
  2026-10-24.
- Full real-Chromium suite: 22 passed; topic creation resolved Total entries to
  1 and refreshed Upcoming above 0 without a reload.
