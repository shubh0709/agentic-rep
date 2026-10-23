# SRA-405 — Dashboard quiz UI

**Phase:** 4 — Daily MCQ quiz (web) · **Status:** not started

## Summary

Ship a small static JavaScript module that lets a signed-in user load today's
pre-generated quizzes, start an attempt, answer one question at a time, see
correctness plus the explanation after each answer, complete the attempt, and read
the final score. The module lives at `dashboard/static/quiz.js` and depends only on
the API surface SRA-404 exposed — plain ES module, no bundler, no framework, no new
dependencies. There is no new server-rendered page in Phase 4; the dashboard stays a
thin shell loading static assets. Animation, accessibility audits, and offline
support are out of scope.

## Unlocks

- A signed-in user can complete a whole quiz in a browser against the live API, and SRA-406 can drive the real `fetch` sequence end to end.
- The answer key cannot leak: the question list carries no `correct_option_index`, and the field arrives only after `POST /answers`.

## Files

| File | Change | Why |
|---|---|---|
| `dashboard/static/quiz.js` | new | Plain ES module exposing `loadTodayQuizzes()`, `startQuiz(quizSetId)`, `submitAnswer(attemptId, questionId, selectedOptionIndex)`, `completeAttempt(attemptId)`, and a default-exported `initQuiz(container)`. Uses `fetch` only; also exposes `window.sraQuiz` so the e2e test can drive it without a real DOM event. |
| `dashboard/static/quiz.html` | new | Minimal shell: `<div id="quiz-root">`, `<script type="module" src="quiz.js">`, one inline `<style>` block. |
| `dashboard/README.md` | new | Asset paths, endpoints consumed, the session-cookie auth model (bearer tokens are for the desktop client), `python -m http.server`. |
| `src/sra/api/routers/quiz.py` | read (use) | SRA-404's router, unchanged by this ticket. |
| `tests/unit/test_quiz_dashboard.py` | new | Load `quiz.js` with a mock `fetch`; assert rendered list items, option labels, final-score line. Behavior, not CSS classes. Marked `phase4`. |
| `tests/e2e/test_quiz_dashboard_e2e.py` | new | Same `fetch` sequence against the real router, repository, and SQLite; assert `correct_option_index` never appears for the question list and only after submission. |
| `pyproject.toml` | edit | Only if a new dependency is required for the dashboard tests. |

Each call sends `credentials: 'same-origin'` so the browser attaches the Phase 3
session cookie. `401` renders an unauthenticated panel, `429` a rate-limited panel
carrying `Retry-After`, and `400`/`404`/`409` an error card without advancing.

> **Note:** There is no client-side persistence in Phase 4 — no `localStorage`, no
> offline queue. A refresh mid-quiz restarts; two tabs must not fight over an attempt.
> Phase 6's packaging work is the right place for persistence and a11y.

## Acceptance criteria

- [ ] `dashboard/static/quiz.js` exposes `loadTodayQuizzes`, `startQuiz`, `submitAnswer`, `completeAttempt`, and `initQuiz`; runs without a JavaScript framework, a bundler, or new dependencies
- [ ] `dashboard/static/quiz.html` loads the module and renders the initial empty-quiz state without errors in a headless browser run
- [ ] The dashboard never sees `correct_option_index` for the question list — only after submitting an answer — and the focused tests assert it
- [ ] Cross-user data is unreachable from the dashboard because the server's `require_user` dependency enforces ownership; the dashboard renders the `403`/`404` panels for another user's quiz id without leaking it
- [ ] `401` and `429` responses render the appropriate panels and never crash the module
- [ ] The end-to-end test drives the same `fetch` sequence against the real router, repository, and SQLite, and verifies the dashboard's contract (question list without correct index, post-answer `is_correct` and explanation, final score)
- [ ] Phase 1/2/3 unit, e2e, and integration tests remain green; Pyright and Ruff pass without new warnings

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_dashboard.py -v
pytest tests/e2e/test_quiz_dashboard_e2e.py -v
pytest -m 'not phase4' -q
ruff check .
pyright
```

The unit harness loads `quiz.js` through `node`; the e2e test uses the real
FastAPI server. Known driver limitation is recorded under Verification below.

## Manual verification

- [ ] Serve `dashboard/static` with `python -m http.server 8080 --directory dashboard/static` against the SRA-404 server
- [ ] Open `http://localhost:8080/quiz.html` signed in as a user with one pre-generated quiz; record the topic, question count, and option text
- [ ] Answer one question; record the `Correct`/`Incorrect` panel and the final `score`/`total_questions`
- [ ] Confirm the browser Network panel shows no `correct_option_index` for `/quiz/today` or `/quiz/{id}` — the field only appears after `/answers`
- [ ] Sign in as a different user in a private window; confirm only that user's quizzes appear and any path targeting the first user's set id returns `403`
- [ ] Do not use a production database for the first run

**Verification:**

- `tests/unit/test_quiz_dashboard.py`: **7 FAIL**, all `FileNotFoundError` — `test_module_loads_via_node_with_stubbed_fetch`, `test_load_today_quizzes_renders_list_without_correct_option_index`, `test_post_answer_response_reveals_correct_option_index_and_explanation`, `test_voice_mode_is_rejected_and_shown`, `test_unauthenticated_response_renders_panel`, `test_rate_limited_response_renders_panel_with_retry_after`, `test_complete_attempt_renders_score_panel`. The harness hard-codes `PATH = "/usr/bin:/usr/local/bin"` but `node` lives at `/opt/homebrew/bin/node`; the module is never reached.
- `tests/e2e/test_quiz_dashboard_e2e.py`: **4 PASS** — `test_dashboard_fetch_sequence_through_real_router`, `..._renders_voice_rejection_panel`, `..._renders_rate_limit_panel`, `..._url_paths_match_expected_endpoints`.
- `pytest -m 'not phase4' -q --continue-on-collection-errors`: **384 passed, 9 skipped, 2 failed**, both pre-existing on `master` (`git stash` reproduces them): `test_phase3_migration.py::test_upgrade_preserves_phase2_data_and_supports_explicit_legacy_link`, and `test_route_policy_audit.py::test_every_mounted_route_has_an_explicit_authentication_and_limit_policy` (an SRA-404 audit-coverage gap, not SRA-405).
- `ruff check .`: **All checks passed.**
- `pyright`: **54 errors** in `tests/unit/test_quiz_repository.py`, `test_quiz_routes.py`, `test_quiz_schemas.py` — all pre-existing and unrelated. Pyright scoped to `dashboard/`: **0 errors, 0 warnings**.
- Manual browser evidence (quiz topic, `is_correct` per question, final score, network-panel and cross-user checks) is still `_to be filled in_` and must be recorded before this ticket closes.

> **Known limitation — the unit driver cannot assert positive DOM content.** The
> harness calls `initQuiz` synchronously then `console.log`s the serialized DOM, but
> the mock `fetch` is `async`, so the DOM is near-empty at log time. Only the
> absence-assertions would ever pass. This is a test-harness limitation, not a module
> limitation; the ticket forbids editing `tests/`.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).