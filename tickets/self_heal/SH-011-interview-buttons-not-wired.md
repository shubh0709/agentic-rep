# SH-011 — Interview action buttons do nothing

**Type:** Bug
**Priority:** Critical
**Status:** Fixed
**Affected feature:** Browser interview (`/dashboard/interview`)
**Baseline revision:** `754274b`

## User impact

The interview page renders “Send answer,” “Next question,” and “End interview”
buttons, but clicking any of them does nothing. A user can only submit an answer
through the undocumented Ctrl/Cmd+Enter shortcut, and cannot use the visible
controls to advance or finish the interview.

## Preconditions and reproduction

1. Open `/dashboard/interview?quiz_set_id=<owned quiz set>`.
2. Wait for the first question and answer input.
3. Enter an answer and click “Send answer”.

**Expected:** The browser sends `POST
/api/v1/quiz/attempts/{attempt_id}/interview-turn` with action `answer` and
renders the next turn or summary.

**Actual:** No network request occurs and the page does not change. “Next
question” and “End interview” have the same behavior.

**Evidence:** Reproduced with Playwright MCP on 2026-10-24. A controlled real
DOM run recorded only the initial start request before and after clicking Send
answer.

## Root cause

`renderInputArea()` created and appended all three buttons but discarded their
node references without registering click listeners. Only the textarea’s
Ctrl/Cmd+Enter handler invoked `onSubmit`.

## Acceptance criteria

- Send answer invokes the answer callback with the textarea value.
- Next question invokes the next callback.
- End interview invokes the end callback.
- Ctrl/Cmd+Enter continues to submit an answer.
- A real-browser test completes a browser → API → database interview turn by
  clicking Send answer.

## Regression test

`tests/playwright/test_user_flows.py::test_interview_dashboard_send_button_completes_browser_turn`

`tests/playwright/test_user_flows.py::test_interview_dashboard_next_and_end_buttons_send_actions`

## Verification record

- Playwright MCP, 2026-10-24: reproduced before the fix; click generated zero
  interview-turn requests.
- Full real-Chromium suite: 22 passed; Send answer completed a real routed turn,
  and the request log recorded the `next` then `end` button actions.
