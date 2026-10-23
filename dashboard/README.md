# Dashboard

*Owns the dashboard's asset layout, API surface, auth model, and local dev/build steps. Root install and run instructions live in [`README.md`](../README.md); the API contract lives in [`docs/design/technical-design.md`](../docs/design/technical-design.md).*

The dashboard is a thin browser shell that loads static JavaScript modules and
consumes the quiz API. There are no build tools, no JavaScript frameworks, and
no new Python dependencies: the browser natively loads each module via
`<script type="module">`, and the only transport is `globalThis.fetch`.

## Asset layout

| Path | Role |
|---|---|
| `dashboard/static/quiz.js` | ES module — exposes `initQuiz`, `loadTodayQuizzes`, `startQuiz`, `submitAnswer`, `completeAttempt`. |
| `dashboard/static/quiz.html` | Minimal HTML shell with `<div id="quiz-root">` and an inline `<style>` block for vertical stacking. Renders the today list and the "Start" / "Start interview" buttons per item. |
| `dashboard/static/interview.js` | ES module — exposes `initInterview`, `startInterview`, `sendAnswer`, `sendNext`, `sendEnd`, `resumeInterview`. Drives the Phase 8 chat thread. |
| `dashboard/static/interview.html` | Minimal HTML shell with `<div id="interview-root">`, an inline `<style>` block for the chat layout, and a `<script type="module">` that loads `interview.js`. |
| `dashboard/README.md` | This document. |

The default export of `quiz.js` is `initQuiz(root, doc)`; the default export of
`interview.js` is `initInterview(root, doc, options?)`. Both also surface their
helpers on `window.sraQuiz` / `window.sraInterview` for the end-to-end driver.
Neither module reads `correct_option_index` for the question list — `QuizSetOut`
strips that field on the server, and the dashboard learns correctness only after
`POST /answers` returns.

## API endpoints consumed

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/v1/quiz/today` | List today's `QuizSet`s for the signed-in user. |
| `POST` | `/api/v1/quiz/{quiz_set_id}/attempts` | Start a `web` (MCQ) or `interview` attempt; returns `attempt_id` and the first AI question for interview mode. |
| `GET`  | `/api/v1/quiz/{quiz_set_id}` | Load the question list (without `correct_option_index`). |
| `POST` | `/api/v1/quiz/attempts/{attempt_id}/answers` | Submit one MCQ answer; returns `is_correct`, `correct_option_index`, `explanation`. |
| `POST` | `/api/v1/quiz/attempts/{attempt_id}/complete` | Finalize the MCQ attempt; returns `score`, `total_questions`. |
| `GET`  | `/api/v1/quiz/attempts/{attempt_id}` | Read the persisted MCQ attempt summary. |
| `POST` | `/api/v1/quiz/attempts/{attempt_id}/interview-turn` | Submit a turn (`action`: `answer` / `next` / `end`) for an interview attempt; returns the score, rationale, feedback, the next AI question, and the updated transcript. |
| `GET`  | `/api/v1/quiz/attempts/{attempt_id}/interview` | Resume an in-progress interview; returns `plan`, `turns`, `summary`, `attempt`. |

All requests use `credentials: "same-origin"` so the session cookie set by the Google OAuth flow in [`src/sra/auth/google_oauth.py`](../src/sra/auth/google_oauth.py) is included automatically.

## Auth model

The dashboard authenticates with the **session cookie** only. The cookie is issued during the Google OAuth sign-in flow and is sent automatically by the browser for same-origin requests — the module never reads or stores a bearer token.

`Authorization: Bearer <token>` headers are reserved for the **desktop client** (and the end-to-end tests, which sign their own tokens). The dashboard never sets `Authorization`. The server-side `require_user` dependency enforces ownership, so cross-user data is unreachable from the client.

## Status-code handling

| Status | Dashboard behavior |
|---|---|
| `200`/`201` | Render the next state (list, question, answer result, summary, interview transcript). |
| `400` | Render a "Cannot start (quiz\|interview) (400)" panel with the server's `detail`. |
| `401` | Render a "Not authenticated (401)" panel; the user must sign in. |
| `403` | Render a "Forbidden (403)" panel; the resource belongs to another user. |
| `404` | Render a "Not found (404)" panel; the quiz or attempt does not exist. |
| `409` | Render a "Request failed (409)" panel (attempt already completed). |
| `422` | Render a "Invalid response (422)" panel (server returned an unparsable interview turn). |
| `429` | Render a "Rate limit exceeded (429)" panel; the `Retry-After` header is surfaced when present. |
| Network failure | Render a "Network error" panel; the exception message is included. |

## Interview dashboard

The interview page lives at `/dashboard/interview`. It is a single-page chat
surface that consumes the SRA-805 interview routes (`POST
/api/v1/quiz/{id}/attempts` with `mode: "interview"`,
`POST /api/v1/quiz/attempts/{id}/interview-turn`, and
`GET /api/v1/quiz/attempts/{id}/interview`). The page renders an AI bubble for
each question, a user bubble plus score chip for each answer, collapsible
rationale on demand, two action buttons (`Next question` and `End interview`),
and a final summary panel on completion.

### Starting an interview

The today-list page renders a `Start interview` button next to the existing MCQ `Start` button. Clicking it navigates to `/dashboard/interview?quiz_set_id=<id>`. The page module reads `quiz_set_id` from the URL and calls `POST /attempts` to start the attempt; the response carries the first AI question.

### Resuming an in-progress interview

A user who closes the tab mid-interview can reopen the same URL (the `quiz_set_id` is preserved) and the page module calls `POST /attempts` again, which re-uses the existing `QuizAttempt` row if one is in progress. If the URL also carries an `attempt_id`, the page calls `GET /api/v1/quiz/attempts/{id}/interview` first to render the existing transcript before enabling the input area. The same resume path is used by voice clients that share the SRA-805 routes.

### Resume fetch on page load

When `initInterview(root, doc, { attempt_id })` is called (or the URL contains `attempt_id=...`), the module issues `GET /interview` before rendering the input area. The fetched `plan`, `turns`, and optional `summary` are drawn into the chat thread; the textarea stays disabled until the fetch completes so a mid-refresh user does not type into an empty transcript. A successful response leaves the input area enabled; a `4xx` renders the same `data-panel="status"` shape the MCQ dashboard already uses.

## Local development

From the repository root, serve the static folder with Python's built-in HTTP
server and open the dashboard in a browser signed in to a user with at least
one pre-generated quiz:

```bash
python -m http.server 8080 --directory dashboard/static
# Open http://localhost:8080/quiz.html in a browser.
```

In production the same files are served through the FastAPI process at `/dashboard-static/` and the page shells at `/dashboard/interview`.

The dashboard is read-and-call only: it fetches from the FastAPI app running
behind the same origin (or proxied via the local dev server) and stores no
progress in `localStorage`. Refreshes mid-quiz discard the in-progress MCQ
attempt; this is intentional for Phase 4. The interview surface intentionally
exposes a resume path because the per-turn LLM cost makes losing an
in-progress attempt expensive.