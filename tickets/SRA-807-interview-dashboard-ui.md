# SRA-807 — Interview dashboard UI

**Phase:** 8 — Interactive AI interview mode · **Status:** not started

## Summary

Adds the browser-side UI for the interview feature: a chat-thread view in `dashboard/static/interview.html` and `dashboard/static/interview.js` that starts an interview, drives the per-turn loop, and renders the summary panel on completion. The JS module reuses the helper style established by `dashboard/static/quiz.js` (`el()`, `clearChildren()`, `statusPanel()`, `handleErrorStatus()`) so the two surfaces look and read consistently. A "Start interview" button is added to the existing `dashboard/static/quiz.html` today list alongside the MCQ "Start" button. UI only: no HTTP handler and no LLM call is touched.

## Unlocks

- A signed-in user can open `/dashboard/interview?quiz_set_id=…` — or click "Start interview" from the today list — and drive a full interview loop in the browser, with the same auth / rate-limit / error UX the MCQ surface already has.
- Closing the tab mid-interview and reopening the URL restores the transcript: the page fetches on load and only enables the input area once the resume fetch completes.

## Files

| File | Change | Why |
|---|---|---|
| `dashboard/static/interview.html` | new | Page shell: single `<div id="interview-root">` plus the boot pattern `quiz.html` uses, loading `interview.js` as an ES module. |
| `dashboard/static/interview.js` | new | Exports `initInterview(root, doc, options?)` on `globalThis.initInterview` plus a `sraInterview` API (`startInterview`, `sendAnswer`, `sendNext`, `sendEnd`, `resumeInterview`, `renderThread`, `renderSummary`, `renderInputArea`, `state`, `ready`) mirroring `quiz.js`. `el` / `clearChildren` / `statusPanel` / `statusTitle` / `friendlyDetail` / `handleErrorStatus` are copied from `quiz.js` verbatim — duplication is intentional, deferred until a third page needs a shared `sra_dom.js`. |
| `dashboard/static/quiz.html` + `quiz.js` | edit | Add a "Start interview" button beside the MCQ "Start"; `renderTodayList` renders a second `data-action="start-interview"` button inside the existing `data-field="actions"` span, leaving the MCQ selector intact. |
| `web/routes.py` | edit | Add `interview_page` returning `interview.html` through a `_read_static_file` helper so the page keeps the same `require_user` + `limit_authenticated` dependencies as the other protected pages; mount `dashboard/static/` at `/dashboard-static` so the module script resolves in production. |
| `tests/unit/test_interview_dashboard.py` | new | Load `interview.js` via `new Function` with a fake `document` and `fetch`; drive start → 2 answers → next → 1 answer → end; assert the `data-panel` attributes, the score chip, the button states, and the summary panel. |
| `tests/e2e/test_phase8_interview_dashboard_e2e.py` | new | Real `create_app()` with a patched LLM, plus the Node module-load smoke test. |
| `dashboard/README.md` | edit | Document the interview page, the `?attempt_id=` resume flow, and the SRA-805 endpoints consumed. |

## Design notes

- **A separate page rather than embedding in `quiz.html`.** The chat-thread UX is structurally different (persistent transcript, two action buttons); embedding would force one page to ship two renderers. `interview.html` keeps each surface single-purpose and lets unit tests target `interview.js` alone.
- **`initInterview` resolves ids in order:** `options`, then `globalThis.location.search` (so both `?quiz_set_id=X` and `?attempt_id=Y` work), then a 400 status panel if neither is present. The resume fetch renders the transcript before enabling the input area, so a dropped connection recovers with no client-side state machine.
- **Actions** hit the SRA-805 routes with `credentials: "same-origin"`: `startInterview` → `POST /api/v1/quiz/{id}/attempts {mode: "interview"}`; `sendAnswer` / `sendNext` / `sendEnd` → `POST …/interview-turn` with `{action, answer_text?}`; `resumeInterview` → `GET …/interview`.

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

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_interview_dashboard.py -v
pytest tests/e2e/test_phase8_interview_dashboard_e2e.py -v
pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py
ruff check .
pyright
```

`tests/unit/test_quiz_dashboard.py` and `tests/e2e/test_quiz_dashboard_e2e.py` are the closest analogues and are reused directly.

## Manual verification

With a real OpenAI key and the SRA-805 routes in place, sign in as a user with at least one `QuizSet` row:

- [ ] From `/dashboard`, click "Start interview": the URL becomes `/dashboard/interview?quiz_set_id=…` and the first question renders. Answer with Enter and confirm the user bubble, score chip, feedback, and next question appear. "Next question" advances the outline; "End interview" replaces the input area with the summary panel (`score`, `answered_count`, `remaining_count`). Refresh (the URL still has `attempt_id`) and confirm the transcript renders before the input enables. A second user in a private window gets `403` with the friendly status panel.

**Verification (2026-10-15, darwin / Python 3.13.12):**

- No live OpenAI call — the e2e test exercises the SRA-805 routes with a patched `_get_chat_model()`.
- `tests/unit/test_interview_dashboard.py` → **7 passed** (0.17 s): module-loads-via-node, start-hits-create-attempt-route, score chip + user bubble, summary panel, and 401 / 429 / wrong-mode-400 status panels.
- `tests/e2e/test_phase8_interview_dashboard_e2e.py` → **2 passed** (0.60 s): full loop through the real `create_app()`, plus the Node module load.
- Regression `pytest -m 'not phase8' -q --ignore=…` → **785 passed / 24 failed / 14 skipped / 153 deselected** — identical to the SRA-805 / SRA-806 / SRA-808 baseline; no new regressions.
- Hand-driven browser loop: **not executed**. Unit + e2e cover the happy path, score chip + `data-field="next-question"` bubble, the three `data-action` button states, the `data-panel="summary"` shape, the `?attempt_id=…` resume, and the 401/403/404/409/422/429 panels.
- `ruff check .` → **All checks passed!** `pyright` → **10 errors, 0 new** (`interview_planner.py:198` SRA-802, `api/routers/dev.py:52/178` SRA-701, 7 in `test_auth_client.py`); modified files clean.
- **Deviations:** the pre-written driver dumped the DOM too early (Node drains microtasks at end of tick), so it now defers inside `setImmediate(...)` and passes a test-only `auto_advance: true` option — `false` by default, so browser use is unaffected. The driver's fake `root` has no `appendChild`, so `interview.js` installs a shim.
- > **Note:** the status line still reads `Planned`.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).