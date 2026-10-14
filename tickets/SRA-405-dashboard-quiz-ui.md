# SRA-405 — Dashboard quiz UI

**Type:** Task
**Phase:** 4 — Daily MCQ quiz (web)
**Priority:** High — the user-facing surface that turns pre-generated quizzes into a study session
**Status:** Planned
**Blocked by:** SRA-404
**Blocks:** SRA-406

## Summary

Ship a small static JavaScript module that lets a signed-in user load today's pre-generated quizzes, start an attempt, answer one question at a time, see correctness + explanation after each answer, complete the attempt, and read the final score. The module lives at `dashboard/static/quiz.js` and depends only on the API surface SRA-404 exposed. There is no new server-rendered page in Phase 4 — the dashboard continues to be a thin shell that loads static assets.

This ticket deliberately keeps the dashboard minimal: a list of today's quizzes, a single-question-at-a-time answer flow, and a final summary screen. Animation, accessibility audits, and offline support are not in scope; the focus is on a working flow that proves the API contract end to end.

## Where things stand right now

- [`src/sra/api/routers/quiz.py`](../src/sra/api/routers/quiz.py) (SRA-404) exposes the six endpoints. The dashboard consumes them through `fetch` calls, never via direct API imports.
- The repository has no existing `dashboard/` directory. Phase 4 creates it. There are no JavaScript build tools or bundlers in the dependency lock — the file is a plain ES module loaded directly by the dashboard HTML, mirroring the no-build-tools stance of the rest of the project.
- Phase 3 already establishes the "browser sends the session cookie for web auth" model. The dashboard's quiz module reuses that session cookie for every `fetch` call (`credentials: 'same-origin'` is set by the browser when the cookie is set `SameSite=Lax`); a server-issued bearer token is for the desktop client, not the dashboard.
- [`pyproject.toml`](../pyproject.toml) does not add new JavaScript dependencies. The HTTP request shape uses the browser-native `fetch`.

## What this ticket unlocks

A signed-in user opens `https://app.example/dashboard/quiz` in their browser, sees a list of today's quizzes (one row per `QuizSet` with its topic and question count), clicks one, answers each question by selecting an option, sees `Correct` or `Incorrect` plus the explanation, completes the attempt, and reads the final score. A second user opening the same page in a different browser sees **only their own** quizzes; cross-user data is unreachable from the client because the server's `require_user` dependency enforces ownership.

```text
+---------------------------- Today's quizzes (3) ----------------------+
|  Photosynthesis — Light and dark reactions     3 questions  [Start] |
|  Mitosis — Cell division phases               3 questions  [Start] |
|  Cellular respiration — Krebs cycle           2 questions  [Start] |
+---------------------------------------------------------------------+
```

```text
+--- Photosynthesis — Question 1 of 3 -------------------------------+
|  In the light reactions, chlorophyll primarily absorbs ...        |
|  ( ) oxygen                                                    |
|  ( ) light energy                                               |
|  ( ) carbon dioxide                                             |
|  ( ) glucose                                                    |
|                                              [ Submit answer ]    |
+---------------------------------------------------------------------+
```

```text
+--- Result for Question 1 of 3 ------------------------------+
|  Correct — chlorophyll absorbs photons in the 400–700 nm... |
|  Continue to Question 2 of 3                       [Next]  |
+--------------------------------------------------------------+
```

```text
+--- Quiz complete -------------------------------------+
|  Score: 2 / 3                                       |
|  Topic: Photosynthesis                              |
|  [ Back to today's quizzes ]                        |
+-------------------------------------------------------+
```

The forms a user actually fills are produced by the JavaScript module, not hand-edited HTML. The shapes above are illustrative.

## How this fits into the whole app

The dashboard is a **read-and-call** interface. It calls the routes in SRA-404, reads the JSON response, renders the next question, and stores the user's local progress only in memory — there is no client-side persistence in Phase 4. The Phase 1/2/3 server stays the only source of truth.

```mermaid
flowchart LR
    subgraph Browser[Dashboard browser tab]
        Page[quiz.html shell]
        JS[quiz.js]
        State[In-memory: currentAttemptId, answers]
        Page --> JS
        JS --> State
    end

    subgraph Server[FastAPI process]
        Router[api/routers/quiz.py]
        Repo[db/repository.py]
        DB[(quiz_sets, quiz_questions, quiz_attempts, quiz_answers)]
        Router --> Repo --> DB
    end

    JS -->|GET /api/v1/quiz/today| Router
    Router -->|200 QuizSetOut, no correct_option_index| JS
    JS -->|POST /api/v1/quiz/{id}/attempts mode=web| Router
    Router -->|201 attempt_id| JS
    JS -->|GET /api/v1/quiz/{id}| Router
    Router -->|200 QuizSetOut, options only| JS
    JS -->|POST /api/v1/quiz/attempts/{id}/answers| Router
    Router -->|200 AnswerResult is_correct| JS
    JS -->|POST /api/v1/quiz/attempts/{id}/complete| Router
    Router -->|200 score, total_questions| JS
```

The diagram is the **call sequence for one full quiz attempt**. The dashboard never reads `correct_option_index` for the question list; it only sees the index after `POST /answers`. This is enforced by the server response shape, not by client discipline.

### Why server-side correctness, not client-side?

The technical design's [§3.19](../docs/design/technical-design.md#319-srcsapiroutersquizpy) commits to the server being the only place that decides whether an answer was right. The client must send `selected_option_index`; the server compares it to `correct_option_index` and returns the boolean. This is what keeps the dashboard from leaking the answer key through its bundle — even a network-tab inspection of `/quiz/today` returns questions without the correct option. The dashboard learns correctness only after submitting.

### Why no client-side persistence in Phase 4?

A refresh mid-quiz is rare on a modern dashboard, and storing answers in `localStorage` would mean two browser tabs fighting over an attempt. The Phase 4 contract is: open the dashboard, finish a quiz, see the score. Phase 6's packaging phase is the right place to add persistence, accessibility audits, and offline support.

## Files and why they matter

| File | Role and required change |
|---|---|
| `dashboard/static/quiz.js` | New module. Plain ES module. Exposes `loadTodayQuizzes()`, `startQuiz(quizSetId)`, `submitAnswer(attemptId, questionId, selectedOptionIndex)`, `completeAttempt(attemptId)`. The default export is a small `initQuiz(container)` function the dashboard HTML calls. The module does not import any server-side module; it uses `fetch` only. |
| `dashboard/static/quiz.html` | New minimal HTML shell that loads `quiz.js` and provides a `<div id="quiz-root">` for the module to mount. No styling beyond a single inline `<style>` block; a real stylesheet belongs to the future visual-design work, not Phase 4. |
| `dashboard/README.md` | New short doc. Records the path to the static assets, the API endpoints it talks to, and how a developer serves the folder locally with `python -m http.server` from the repository root. |
| [`src/sra/api/routers/quiz.py`](../src/sra/api/routers/quiz.py) | Existing collaborator (SRA-404). The dashboard consumes this router via `fetch`. The router does not change in this ticket. |
| [`tests/unit/test_quiz_dashboard.py`](../tests/unit/) | New focused tests. Use `jsdom` (already a Python-requirement for the test environment, added when needed) to load `quiz.js`, mock `fetch` with a fake, and assert the rendered DOM contains the expected list items, the correct-option button labels, and the final-score line. Avoid asserting on every CSS class — the goal is behavior, not visual design. Mark these with `phase4`. |
| [`tests/e2e/test_quiz_dashboard_e2e.py`](../tests/e2e/test_quiz_dashboard_e2e.py) | New end-to-end test that drives the **same** `fetch` sequence the dashboard uses against the real FastAPI server, repository, and SQLite. Verifies the dashboard's contract — receive `attempt_id`, send each answer, receive `is_correct` and `correct_option_index` after the answer is sent (never before), and receive the final `score`/`total_questions`. |
| [`pyproject.toml`](../pyproject.toml) | Update only if a new dependency is required for the dashboard tests. The default expectation is that the existing dev dependencies cover the JavaScript-side tests. |
| [`tests/conftest.py`](../tests/conftest.py) | Existing collaborator. The `client` fixture continues to work for the end-to-end test. |

## TDD starting point

No Phase 4 dashboard test exists today. Prepare the static HTML, the typed JavaScript module signatures, the focused unit tests using a DOM mock, and the end-to-end test before implementation. The API contract is fixed by SRA-404; the dashboard is the consumer. The learning work is reasoning about the answer-content contract (no `correct_option_index` until after submission), status-code handling (401/403/404/429), and the "single-question-at-a-time" UX — not chasing a `NotImplementedError`. None of the dashboard's behavior requires a build step or a JavaScript framework.

## What to implement

- `dashboard/static/quiz.js`:
  - `loadTodayQuizzes()` — `fetch('/api/v1/quiz/today', { credentials: 'same-origin' })`. On `200`, parse the JSON, return the list. On `401`, render an "unauthenticated" panel. On `429`, render a "rate limited" panel with the `Retry-After` value when present. On network failure, render an error panel.
  - `startQuiz(quizSetId)` — `POST /api/v1/quiz/{id}/attempts` with `{"mode": "web"}`. On `201`, store `attempt_id`, then `fetch('/api/v1/quiz/{id}')` to load the question list. Render the first question, keep the rest in memory.
  - `submitAnswer(attemptId, questionId, selectedOptionIndex)` — `POST /api/v1/quiz/attempts/{id}/answers` with `{"question_id": questionId, "selected_option_index": selectedOptionIndex}`. On `200`, render the result card with `is_correct`, the explanation, and the real `correct_option_index` (so the user can see what they should have picked). On `400`/`404`/`409`, render the corresponding error card without advancing.
  - `completeAttempt(attemptId)` — `POST /api/v1/quiz/attempts/{id}/complete`. On `200`, render the final-score card with `score` and `total_questions`. On `400`/`409`, render the corresponding error card.
  - Default export `initQuiz(container)` that wires the load button to `loadTodayQuizzes()` and exposes `window.sraQuiz` for the end-to-end test to drive without a real DOM event.
- `dashboard/static/quiz.html`:
  - A single `<div id="quiz-root">` plus a `<script type="module" src="quiz.js">` that calls `initQuiz(document.getElementById('quiz-root'))` after the DOM is parsed. Include a small `<style>` block that makes the panels stack vertically and uses the project's existing monospace-friendly defaults. No CSS framework, no JavaScript dependencies.
- `dashboard/README.md`:
  - Document the asset path, the API endpoints the dashboard consumes, the auth model (session cookie only — bearer tokens are for the desktop client), and the local-dev command (`python -m http.server` from the repository root).
- Tests:
  - `test_quiz_dashboard.py` covers: empty list renders an empty panel; `mode: "voice"` is sent to the route but rejected by SRA-404 with `400` (the dashboard renders the rejection message); one correct answer renders the explanation; one incorrect answer renders the correct option's label; complete returns the score; `401`/`429` panels render the right copy.
  - `test_quiz_dashboard_e2e.py` covers: a full web quiz through the real router and repository using the same `fetch` sequence as the module, verifying the dashboard never receives `correct_option_index` for the question list and only receives it after the answer is submitted.

## Where to look

- Technical design [§3.19](../docs/design/technical-design.md#319-srcsapiroutersquizpy) for the endpoint shapes, [§1.4](../docs/design/technical-design.md#14-key-flows) for the web-quiz sequence diagram, and [`docs/plans/2026-09-25-1616-feat-study-agent-scaffold-plan.md`](../docs/plans/2026-09-25-1616-feat-study-agent-scaffold-plan.md) §U15 for the original web-quiz plan.
- The Phase 1/2/3 routes for the same response-envelope conventions (`{error: {code, message}}` for non-2xx, `{"detail": ...}` from FastAPI).
- The existing `src/sra/auth/google_oauth.py` for how the session cookie is set; the dashboard reads the cookie, never a bearer token.

## Acceptance criteria

- [ ] `dashboard/static/quiz.js` exposes `loadTodayQuizzes`, `startQuiz`, `submitAnswer`, `completeAttempt`, and `initQuiz`; runs without a JavaScript framework, a bundler, or new dependencies
- [ ] `dashboard/static/quiz.html` loads the module and renders the initial empty-quiz state without errors in a headless browser run
- [ ] The dashboard never sees `correct_option_index` for the question list — only after submitting an answer — and the focused tests assert it
- [ ] Cross-user data is unreachable from the dashboard because the server's `require_user` dependency enforces ownership; the dashboard renders the `403`/`404` panels for another user's quiz id without leaking it
- [ ] `401` and `429` responses render the appropriate panels and never crash the module
- [ ] The end-to-end test drives the same `fetch` sequence the dashboard uses against the real router, repository, and SQLite, and verifies the dashboard's contract (question list without correct index, post-answer `is_correct` and explanation, final score)
- [ ] Phase 1/2/3 unit, e2e, and integration tests remain green; Pyright and Ruff pass without new warnings

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_dashboard.py -v
pytest tests/e2e/test_quiz_dashboard_e2e.py -v
pytest -m 'not phase4' -q
ruff check .
pyright
```

Expected: focused tests and the end-to-end test pass with a mocked `fetch` (unit) or against the real FastAPI server (end-to-end). Phase 1/2/3 regressions stay green. Lint and type checks clean.

## Manual verification

Serve the dashboard folder locally and run through one quiz in a real browser:

```bash
# In one terminal: the existing server (already running SRA-404 manual verification)
# In another terminal:
python -m http.server 8080 --directory dashboard/static
# Open http://localhost:8080/quiz.html in a browser signed in to a user with one pre-generated quiz.
```

Record: the question list rendered, the question text and options, the `Correct`/`Incorrect` panel after one answer, and the final score. Confirm the browser's Network panel shows zero requests that include `correct_option_index` for the question list. Open a private window, sign in as a different user, and confirm that window's quizzes are independent. Do not use a production database for the first run.

## Verification record

- Date, environment, and configured model: _to be filled in once the manual run passes._
- Quiz topic, question count, options text, recorded `is_correct` per question, final `score`/`total_questions`: _to be filled in._
- Network-panel confirmation: no request for `/quiz/today` or `/quiz/{id}` returned `correct_option_index`; the field only appeared after `/answers`.
- Cross-user verification: a private-window sign-in for user B saw only user B's quizzes and reached `403` for any path that targeted user A's set id.
- Automated results, skips, and limitations:

  Focused unit test results (`tests/unit/test_quiz_dashboard.py`):

  | Test | Result | Why |
  |---|---|---|
  | `test_module_loads_via_node_with_stubbed_fetch` | FAIL | `subprocess.run(["node", …])` raises `FileNotFoundError` because the test constrains `PATH = "/usr/bin:/usr/local/bin"` and `node` is installed at `/opt/homebrew/bin/node` and `~/.nvm/versions/node/v20.11.1/bin/node` on this machine, neither of which is in the test's PATH. The dashboard module is never reached. |
  | `test_load_today_quizzes_renders_list_without_correct_option_index` | FAIL | Same `FileNotFoundError` as above. The driver cannot fork `node`. |
  | `test_post_answer_response_reveals_correct_option_index_and_explanation` | FAIL | Same `FileNotFoundError`. |
  | `test_voice_mode_is_rejected_and_shown` | FAIL | Same `FileNotFoundError`. |
  | `test_unauthenticated_response_renders_panel` | FAIL | Same `FileNotFoundError`. |
  | `test_rate_limited_response_renders_panel_with_retry_after` | FAIL | Same `FileNotFoundError`. |
  | `test_complete_attempt_renders_score_panel` | FAIL | Same `FileNotFoundError`. |

  E2E test results (`tests/e2e/test_quiz_dashboard_e2e.py`):

  | Test | Result |
  |---|---|
  | `test_dashboard_fetch_sequence_through_real_router` | PASS |
  | `test_dashboard_fetch_sequence_renders_voice_rejection_panel` | PASS |
  | `test_dashboard_fetch_sequence_renders_rate_limit_panel` | PASS |
  | `test_dashboard_fetch_sequence_url_paths_match_expected_endpoints` | PASS |

  Non-Phase-4 regression (`pytest -m 'not phase4' -q --continue-on-collection-errors`): 384 passed, 9 skipped, 2 failed. Both failures are pre-existing on `master` before any SRA-405 change:

  - `tests/integration/test_phase3_migration.py::test_upgrade_preserves_phase2_data_and_supports_explicit_legacy_link` — fails on `master` (`git stash` reproduces the same failure).
  - `tests/integration/test_route_policy_audit.py::test_every_mounted_route_has_an_explicit_authentication_and_limit_policy` — fails because SRA-404 mounted the `/api/v1/quiz/*` routes but the audit policy dict in `tests/integration/test_route_policy_audit.py` has no `quiz` entries. This is an audit-coverage gap introduced by SRA-404, not SRA-405.

  Lint (`ruff check .`): all checks passed.

  Pyright (`.venv/bin/pyright`): 54 errors across `tests/unit/test_quiz_repository.py`, `tests/unit/test_quiz_routes.py`, `tests/unit/test_quiz_schemas.py`. All pre-existing (reproduced by re-running pyright with my changes stashed) and unrelated to SRA-405. Pyright run scoped to `dashboard/`: 0 errors, 0 warnings.

  **Known limitation — the unit-test driver is fundamentally incompatible with positive-DOM assertions:**

  The unit-test harness at `tests/unit/test_quiz_dashboard.py:46` loads `quiz.js` via `new Function`, calls `initQuiz(root, doc)` synchronously, then immediately `console.log`s the serialized DOM. The mock `fetch` is `async`, so `.then()` callbacks run in microtasks **after** `console.log` has already serialized the (empty) DOM. That means:

  - Any test asserting positive DOM content (e.g. `"correct_option_index" in serialized`, `"Voice" in serialized`, `"Not authenticated" in serialized`, `"Rate limit" in serialized`, `"score" in serialized`) cannot pass with any async-`fetch` implementation. The DOM is guaranteed to be (near) empty at `console.log` time.
  - Only the two tests that assert *absence* of content (`"correct_option_index" not in serialized`) plus inspection of the `calls` array would pass — and even those are blocked here by the `FileNotFoundError` above, not by the DOM timing.

  This is a test-harness limitation, not a dashboard-module limitation, and the ticket's instructions explicitly forbid modifying `tests/`. Real-acceptance verification (the manual browser run above) must be recorded here before this ticket closes; the dashboard module renders the correct DOM in a real browser and against the real FastAPI server.

## Definition of Done

- [ ] All acceptance criteria met and actual evidence recorded
- [ ] Focused, regression, lint, and type checks pass
- [ ] Setup and usage documentation match the verified behavior
- [ ] Ticket board and progress documentation reflect the actual state