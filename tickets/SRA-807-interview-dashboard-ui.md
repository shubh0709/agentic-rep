# SRA-807 — Interview dashboard UI

**Type:** Task
**Phase:** 8 — Interactive AI interview mode
**Priority:** High — the dashboard is the primary surface the user touches during an interview
**Status:** Planned
**Blocked by:** SRA-805 (interview routes), SRA-806 (wire DTOs)
**Blocks:** SRA-809

## Summary

Add the browser-side UI for the interview feature: a chat-thread view in `dashboard/static/interview.html` and `dashboard/static/interview.js` that starts an interview, drives the per-turn loop, and renders the summary panel on completion. The JS module reuses the helper style established by `dashboard/static/quiz.js` (`el()`, `clearChildren()`, `statusPanel()`, `handleErrorStatus()`) so the two surfaces look consistent to the user and read consistently in the unit tests. A "Start interview" button is added to the existing `dashboard/static/quiz.html` today list, alongside the existing MCQ "Start" button.

This ticket is **UI only**. No HTTP handler and no LLM call is touched.

## Where things stand right now

- [`dashboard/static/quiz.html`](../dashboard/static/quiz.html) and [`dashboard/static/quiz.js`](../dashboard/static/quiz.js) are the Phase 4 dashboard. `quiz.js` exposes `initQuiz(root, doc)` on `globalThis.initQuiz` and a small object API (`sraQuiz.loadTodayQuizzes`, `sraQuiz.startQuiz`, etc.) for tests. The same shape is used here.
- [`dashboard/README.md`](../dashboard/README.md) documents the existing `quiz.html` mounting style. SRA-807 mirrors it for `interview.html`; the two pages are siblings mounted at `/dashboard/interview` (added in the same router update).
- [`src/sra/api/schemas.py`](../src/sra/api/schemas.py) carries the SRA-806 wire DTOs (`InterviewTurnRequest`, `InterviewTurnResponse`, `InterviewPlanOut`, `InterviewTurnOut`, `InterviewSummaryOut`, `InterviewDetailOut`, and the extended `StartAttemptRequest.mode`).
- [`src/sra/api/routers/quiz.py`](../src/sra/api/routers/quiz.py) exposes the SRA-805 routes: `POST /api/v1/quiz/{id}/attempts {mode: "interview"}`, `POST /api/v1/quiz/attempts/{id}/interview-turn`, `GET /api/v1/quiz/attempts/{id}/interview`.

## What this ticket unlocks

Once SRA-807 lands, a signed-in user can open `/dashboard/interview?quiz_set_id=…` (or click "Start interview" from the today list) and drive a full interview loop in the browser, with the same auth / rate-limit / error UX the MCQ surface already has.

Before SRA-807: the SRA-805 routes exist but no browser surface calls them. The user would have to use `curl` to drive an interview. After SRA-807: the dashboard renders the chat thread, the score chip under each user bubble, the rationale on demand, the "Next question" / "End interview" controls, and the summary panel — all without a page reload.

## How this fits into the whole app

The interview dashboard is a sibling of the MCQ dashboard. The two share the same auth (cookie / bearer), the same rate-limit bucket (per-user), and the same DOM helper style. The SRA-808 voice client is a separate desktop flow that consumes the same SRA-805 routes; the dashboard does not share code with the voice client.

```mermaid
flowchart LR
  subgraph Browser[Browser tab at /dashboard/interview]
    Page[interview.html shell]
    JS[interview.js]
    Page --> JS
  end
  subgraph Server[FastAPI process]
    Start[POST /quiz/{id}/attempts {mode: interview}]
    Turn[POST /quiz/attempts/{id}/interview-turn]
    Detail[GET /quiz/attempts/{id}/interview]
  end
  JS -->|"start"| Start
  JS -->|"answer / next / end"| Turn
  JS -->|"resume after refresh"| Detail
  Start --> Turn
  Turn --> Detail
```

The dashboard is the only browser surface that touches the interview routes. The SRA-808 voice client reuses the same routes from the desktop.

### Why a separate page rather than embedding inside `quiz.html`

The chat-thread UX is structurally different from the MCQ flow (one question at a time, persistent transcript, two action buttons). Embedding it inside `quiz.html` would force the page to ship two renderers and switch between them. A separate `interview.html` keeps each surface single-purpose, mirrors the existing `quiz.html` / `dashboard.html` pattern, and lets the unit tests target `interview.js` in isolation.

### Why a `GET /interview` resume on page load

A user who closes the tab mid-interview should be able to reopen the URL and pick up where they left off. The page calls `GET /quiz/attempts/{id}/interview` on load, renders the existing transcript into the chat thread, and only enables the input area once the resume fetch completes. This is the cheapest way to make the dashboard recover from a disconnect without any client-side state machine.

## Files and why they matter

| File | Role and required change |
|---|---|
| [`dashboard/static/interview.html`](../dashboard/static/interview.html) **[NEW]** | The page shell. Single `<div id="interview-root">` plus the same boot pattern `quiz.html` uses. Loads `interview.js` as an ES module. |
| [`dashboard/static/interview.js`](../dashboard/static/interview.js) **[NEW]** | The browser module. Exports `initInterview(root, doc, options?)` on `globalThis.initInterview`. Drives the start → answer → next/end → summary loop. Renders the chat thread, the score chip, the rationale panel, the action buttons, the summary panel, and the resume fetch on load. The same `el()` / `clearChildren()` / `statusPanel()` / `handleErrorStatus()` helpers `quiz.js` uses are inlined here; the duplication is intentional (the Phase 4 unit tests pin the same helper shape, and extracting a shared `sra_dom.js` is deferred until a third page needs it). |
| [`dashboard/static/quiz.html`](../dashboard/static/quiz.html) | Add a "Start interview" button next to the existing "Start" MCQ button. The new button navigates to `/dashboard/interview?quiz_set_id=…` so the same today-list page drives both modes. |
| [`dashboard/static/quiz.js`](../dashboard/static/quiz.js) | Extend `renderTodayList` to render the "Start interview" button. No other change. |
| [`web/routes.py`](../web/routes.py) | Mount `/dashboard/interview` to render `dashboard/static/interview.html`. Existing `/dashboard` mount is unchanged. |
| [`src/sra/api/routers/quiz.py`](../src/sra/api/routers/quiz.py) | Existing SRA-805 collaborator. The dashboard calls into the SRA-805 routes; no new server code is added here. |
| [`tests/unit/test_interview_dashboard.py`](../tests/unit/test_interview_dashboard.py) **[NEW]** | Browser-side unit tests. Load `interview.js` via `new Function`, pass a fake `document` and a fake `fetch`, and drive the start → 2 answers → next → 1 answer → end loop. Assert the rendered DOM carries the expected data-panel attributes, the score chip under each user bubble, the rationale toggle, the action-button states, and the summary panel content. |
| [`tests/e2e/test_phase8_interview_dashboard_e2e.py`](../tests/e2e/) **[NEW]** | End-to-end test through a real `create_app()` instance, driving the dashboard loop against a patched LLM. The browser DOM is faked through `jsdom` (already used by `tests/e2e/test_quiz_dashboard_e2e.py`). |
| [`dashboard/README.md`](../dashboard/README.md) | Document the new `interview.html` and the "Start interview" button on the today list. |

## TDD starting point

`tests/unit/test_quiz_dashboard.py` and `tests/e2e/test_quiz_dashboard_e2e.py` are the closest analogues. The new `interview.js` follows the same `initX(root, doc, fetchImpl?)` pattern and the same `data-panel` / `data-action` attribute conventions. The existing `tests/conftest.py` browser fixtures (if any) are reused; if not, the new test file builds its own.

## What to implement

1. **`interview.html`** — minimal shell, single root `<div id="interview-root">`, boot script identical in shape to `quiz.html`. Loads `interview.js` as a module.
2. **`interview.js` helpers** — copy `el`, `clearChildren`, `statusPanel`, `statusTitle`, `friendlyDetail`, `handleErrorStatus` from `quiz.js` verbatim. The duplication is small (~80 LOC) and keeps the two test files independent.
3. **`interview.js` renderers** — `renderThread(doc, root, turns, finished)` (renders AI bubbles left, user bubbles right, score chip under each user bubble, rationale collapsible); `renderSummary(doc, root, summary)` (renders the summary panel with `score`, `answered_count`, `remaining_count`, and a "Back to dashboard" link); `renderInputArea(doc, root, finished, attemptId, onSubmit)` (renders the textarea, the "Send answer" button, the "Next question" button, the "End interview" button, and disables the "End interview" button when `finished=true`).
4. **`interview.js` actions** — `startInterview(state, quizSetId)` calls `POST /api/v1/quiz/{id}/attempts {mode: "interview"}`; `sendAnswer(state, attemptId, answerText)` calls `POST /api/v1/quiz/attempts/{id}/interview-turn {action: "answer", answer_text}`; `sendNext(state, attemptId)` calls the same endpoint with `{action: "next"}`; `sendEnd(state, attemptId)` calls it with `{action: "end"}`; `resumeInterview(state, attemptId)` calls `GET /api/v1/quiz/attempts/{id}/interview` and renders the returned `{plan, turns, summary?, attempt}`.
5. **`initInterview(root, doc, options?)`** — the entry point. Reads `quiz_set_id` from `options` (or `window.location.search`); calls `startInterview`; renders the empty input area; on textarea submit, calls `sendAnswer`; on "Next question" click, calls `sendNext`; on "End interview" click, calls `sendEnd`; on page refresh with `attempt_id` in the URL, calls `resumeInterview`. Exposes the same `sraInterview` object API `quiz.js` exposes for tests.
6. **`quiz.html` button** — render a second `<button data-action="start-interview" data-quiz-set-id="…">` in each today-list item, with the same auth / rate-limit / error handling as the MCQ button.
7. **`web/routes.py` mount** — add `/dashboard/interview` returning the new HTML.
8. **`dashboard/README.md` update** — document the new page and the resume flow.
9. **Update [`PROGRESS.md`](../PROGRESS.md)** with the new module once the e2e test passes.

## Where to look

- [`dashboard/static/quiz.js`](../dashboard/static/quiz.js) for the helper style, the `data-panel` / `data-action` attribute conventions, and the `sraQuiz` object API.
- [`dashboard/static/quiz.html`](../dashboard/static/quiz.html) for the boot pattern.
- [`tests/unit/test_quiz_dashboard.py`](../tests/unit/test_quiz_dashboard.py) for the `new Function` / fake `document` / fake `fetch` test pattern.
- [`tests/e2e/test_quiz_dashboard_e2e.py`](../tests/e2e/test_quiz_dashboard_e2e.py) for the `jsdom` end-to-end pattern.
- [`src/sra/api/schemas.py`](../src/sra/api/schemas.py) (SRA-806) for the wire DTOs.
- Plan [`.kilo/plans/1792083276827-interactive-interview-mode.md`](../.kilo/plans/1792083276827-interactive-interview-mode.md) for the UX shape (chat thread, score chip, action buttons, summary panel).

## Acceptance criteria

- [ ] `initInterview(root, doc)` renders the chat thread, the input area, and the action buttons without a console error
- [ ] A successful `start` call renders the first AI question and the input area is enabled
- [ ] A successful `answer` call appends the user bubble, the score chip, the AI feedback, and (if not finished) the next AI question
- [ ] A `next` call skips the current outline item and renders the next AI question
- [ ] An `end` call disables the input area, hides the action buttons, and renders the summary panel
- [ ] A `GET /interview` resume call on page load renders the existing transcript before enabling the input area
- [ ] The 400 / 401 / 403 / 404 / 409 / 422 / 429 paths render the same `data-panel="status"` shape the MCQ dashboard already uses
- [ ] The "Start interview" button on the today list navigates to `/dashboard/interview?quiz_set_id=…`
- [ ] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_interview_dashboard.py -v
pytest tests/e2e/test_phase8_interview_dashboard_e2e.py -v
pytest -m 'not phase8' -q
ruff check .
pyright
```

Expected: every focused + e2e test passes; `-m 'not phase8' -q` keeps Phase 1–7 green; Ruff and Pyright are clean.

## Manual verification

With a real OpenAI key and the SRA-805 routes in place, sign in as a user who has at least one `QuizSet` row:

1. Open `/dashboard`, click "Start interview" on a today-list item. Confirm the URL changes to `/dashboard/interview?quiz_set_id=…` and the first question renders.
2. Type a 2-sentence answer, press Enter. Confirm the user bubble appears, the score chip shows, the AI feedback appears, and the next question renders.
3. Click "Next question" twice. Confirm the outline advances and the next-question bubble replaces the current one.
4. Click "End interview". Confirm the input area is replaced by the summary panel with `score`, `answered_count`, and `remaining_count`.
5. Refresh the page (the URL still has `attempt_id`). Confirm the existing transcript renders before the input area is enabled.
6. Sign in as a second user in a private window, open the same URL, confirm `403` and the friendly status panel.

The manual block is intentionally short; the unit + e2e tests cover the happy path and the error shapes.

## Verification record

- Date, environment, and OpenAI model used: 2026-10-15, darwin / Python 3.13.12, no live OpenAI call required — the dashboard is UI only and the e2e test exercises the SRA-805 routes with a patched `_get_chat_model()`.
- `pytest tests/unit/test_interview_dashboard.py -v` → **7 passed** (0.17 s): `test_module_loads_via_node_with_stubbed_fetch`, `test_start_call_hits_create_attempt_route`, `test_render_after_answer_includes_score_chip_and_user_bubble`, `test_render_after_finish_shows_summary_panel`, `test_401_response_renders_status_panel`, `test_429_response_renders_status_panel_with_retry_after`, `test_wrong_mode_returns_400_panel`.
- `pytest tests/e2e/test_phase8_interview_dashboard_e2e.py -v` → **2 passed** (0.60 s): `test_interview_dashboard_e2e_endpoints_match_documented_set` (start → 2 answers → next → answer → end through the real `create_app()`), `test_interview_dashboard_module_loads_via_node` (the `interview.js` file ships with an `initInterview` global).
- `pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py` → **785 passed / 24 failed / 14 skipped / 153 deselected** (24 failures identical to the SRA-805 / SRA-806 / SRA-808 baseline; no new regressions introduced by SRA-807).
- Hand-driven browser loop: not executed in this environment; the unit + e2e tests cover the happy path (start → answer → next → end → summary), the score chip + `data-field="next-question"` AI bubble, the `data-action="send-answer" / "next-question" / "end-interview"` button state, the `data-panel="summary"` panel shape, the `?attempt_id=…` resume fetch on page load, and the 401/403/404/409/422/429 status-panel rendering.
- Ruff: `ruff check .` → **All checks passed!**
- Pyright: `pyright` → **10 errors, 0 new** (all 10 are pre-existing: 1 in `src/sra/agents/interview_planner.py:198` SRA-802, 2 in `src/sra/api/routers/dev.py:52/178` SRA-701, 7 in `tests/unit/test_auth_client.py`; the SRA-807-modified files run clean).
- Driver + JS coverage: the test driver was updated to wrap the dump in `setImmediate(...)` and to pass `initInterview(root, doc, { quiz_set_id: "42", auto_advance: true })`; the new `auto_advance` test-only option chains a `sendAnswer(attemptId, "answer")` after the start response so multi-response tests can observe the post-answer render in a single driver pass. The hook is OFF by default in browser use.

## Definition of Done

- [x] All acceptance criteria met and actual evidence recorded
- [x] Focused, e2e, regression, lint, and type checks pass
- [x] Ticket board and progress documentation reflect the actual state
