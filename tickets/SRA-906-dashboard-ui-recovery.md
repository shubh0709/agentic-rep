# SRA-906 — Dashboard UI recovery and topic-generation completion

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** verified

## Summary

Restores the running `/dashboard` experience and finishes the create-topic flow. `dashboard.js` called the modal initializer across two isolated IIFEs, raising `ReferenceError` during module evaluation — that one exception prevented entry loading, search, review statistics, and the modal from starting. Removing it also exposed unfinished modal wiring and invalid quiz/interview links, so a created topic became persisted but unusable. This ticket fixes the JavaScript scope bug, binds modal handlers once, renders actions on every card, runs generation during create, and repairs the routes and test harnesses. An authenticated user now opens `/dashboard` and immediately sees entries and review statistics; they can search, open Create topic, add pasted/URL/upload references, submit once, and receive a generated quiz and interview with working links.

## Unlocks

- SRA-905 can close Phase 9 against a dashboard that actually functions in Chromium.
- Existing cards can regenerate from their saved references without double-charging the review schedule.

## Files

| File | Change | Why |
|---|---|---|
| `web/static/js/dashboard.js` | edit | Keep the helpers/controller in one scope, bind once, recover controls on failure, render actions on all cards, and build valid page links. |
| `src/sra/api/routers/topics.py` | edit | Validate uploads and run generation during create using the same shared generation path as regenerate. |
| `web/static/js/quiz.js` | edit (possible) | Accept an optional `quiz_set_id` query parameter if direct-start navigation is retained. |
| `tests/playwright/test_user_flows.py` | edit | Drive dashboard load, search, modal reopen, create, regenerate, and link navigation in Chromium; repair the tuple-aware ingestion fake. |
| `tests/unit/test_dashboard_phase9_js.py` | edit | Pin script parse/scope, one-time binding, error recovery, and route construction. |
| `tests/e2e/test_phase9_dashboard_create_topic_e2e.py` | edit | Require create-topic generation IDs and correct request failure behavior. |
| `pyproject.toml` | edit | Ensure the installed test entry point can import the browser-facing `web` package. |

## Issue inventory

The 2026-10-17 localhost and real-browser audit found nine issues. Three are product bugs, four are wiring/UX defects, and two are test-double defects:

1. `dashboard.js` aborts at `initCreateTopicModal(createTopicButton)` — the function is declared in a different closure, so entries, search, due/upcoming counts, and Create topic all stay inert.
2. Modal controls are rebound on every open, causing duplicate add/submit/close handlers after reopening.
3. Existing entry cards never receive Regenerate / Add references actions; only a newly prepended card gets one.
4. `POST /api/v1/topics` persists the entry and references but always returns `quiz_set_id=null` and `interview_plan_id=null`, contrary to "Generate now".
5. New-card links use `/quiz/{id}` and `/interview/{plan_id}`, but the mounted pages are `/quiz` and `/dashboard/interview`, and the interview page starts from `quiz_set_id`.
6. Network errors leave create/regenerate controls stuck because the fetch calls are not guarded by `try/finally`.
7. Invalid upload base64 is accepted as a partial-success `201` instead of the documented `422`.
8. The real-browser landing capture assertion is a test-double defect: its fake model ignores tuple-form LangChain messages and saves `Study Note` while the test searches for `Mitosis`.
9. The documented `.venv/bin/pytest` entry point cannot import the top-level `web` package; `python -m pytest` succeeds.

Environment-only observations are **not** product bugs: loopback tests cannot bind inside the default sandbox, and seven legacy dashboard unit tests require a `node` executable that is not installed on this machine.

## Contracts

- Dashboard startup performs the three established reads with no browser console exception.
- One modal open produces one set of handlers; reopening and submitting still produces exactly one request.
- Topic creation either returns usable generation IDs or explicit per-pipeline errors — never silent success with both IDs absent.
- Existing and new cards use only mounted browser URLs; interview startup uses the `quiz_set_id` that `interview.js` requires.
- Fetch rejection and non-2xx responses restore button state and show an inline error without closing the modal.
- Malformed base64 is a `422` client error and does not persist an entry.
- Browser/test fixtures model the production tuple-form prompt correctly.

## Acceptance criteria

- [x] Dashboard entries, search, stats, upcoming reviews, and Create topic work in Chromium with no console/page errors
- [x] Create topic supports paste, URL, and upload payloads and sends exactly one request per submit after any number of modal opens
- [x] Generate now returns a quiz set and interview plan (or explicit partial errors) and renders working actions
- [x] Existing cards can regenerate; ownership and review-schedule invariants remain intact
- [x] Invalid uploads and network/server failures are visible and recoverable
- [x] Both direct `pytest` and `python -m pytest` collect the browser tests

## Tests

```bash
SRA_RUN_PLAYWRIGHT_TESTS=1 python -m pytest tests/playwright/test_user_flows.py -v
python -m pytest tests/unit/test_dashboard_phase9_js.py tests/unit/test_topics_route_regenerate.py -v
python -m pytest tests/e2e/test_phase9_dashboard_create_topic_e2e.py -v
python -m pytest tests/integration/test_phase9_topic_route.py -v
ruff check web src/sra/api/routers/topics.py tests/playwright
pyright web src/sra/api/routers/topics.py tests/playwright
```

The Playwright suite is opt-in via `SRA_RUN_PLAYWRIGHT_TESTS=1` and needs a Chromium install; it is the only check that exercises the real browser surface.

## Manual verification

- [ ] With the service running, confirm `/api/health` and `/` answer, `/dashboard` correctly requires authentication, and an authenticated session in headless Chromium loads entries, search, stats, upcoming reviews, and Create topic with no console error.

**Verification (2026-10-17, macOS, Python 3.13.12, headless Chromium, localhost Uvicorn):**

- **Pre-fix:** established Playwright suite **12 passed, 1 failed**; full suite **1173 passed, 34 failed, 27 skipped**. The full-suite total includes documented pre-existing, Phase 9 WIP, missing-Node, and sandbox-only failures, and is not itself the post-fix target.
- **Post-fix:** focused dashboard/topic suite **22 passed**; Phase 9 marker **227 passed, 3 skipped**; real Chromium user flows **16 passed**; Ruff and Pyright clean on changed surfaces.
- **Post-fix full default suite: 1196 passed, 30 skipped, 17 failed.** The 17 are the already-classified Phase 3/4 migration and generation baseline failures, three sandbox-only loopback failures, and seven legacy Node-harness failures caused by that harness replacing `PATH` with a location that has no Node.
- Authenticated UI behavior was exercised against the same FastAPI app in headless Chromium rather than modifying the user's live account/session.
- **Follow-up regression repair, same day:** initial default run **18 failed, 1213 passed, 30 skipped**. Fixed by making same-day regeneration replace the existing quiz questions and interview outline in place (preserving parent IDs and attempts), correcting attempt totals to count persisted questions after verifier drops, restoring answer-feedback and final-score rendering in the quiz dashboard, and repairing historical migration fixtures/tooling and the Node dashboard test harness **without weakening their behavioral assertions**.
- Unrestricted full suite: **1232 passed, 29 skipped**. Restricted sandbox run: **1228 passed, 30 skipped, 3 failed**, all three caused solely by the sandbox denying ephemeral localhost binds. `ruff check .` clean; `pyright` **0 errors, 0 warnings, 0 informations**.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).