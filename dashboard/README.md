# Dashboard

The dashboard is a thin browser shell that loads a single static JavaScript
module and consumes the SRA-404 quiz API. There are no build tools, no
JavaScript frameworks, and no new Python dependencies: the browser
natively loads the module via `<script type="module">`, and the only
transport is `globalThis.fetch`.

## Asset layout

| Path | Role |
|---|---|
| `dashboard/static/quiz.js` | ES module — exposes `initQuiz`, `loadTodayQuizzes`, `startQuiz`, `submitAnswer`, `completeAttempt`. |
| `dashboard/static/quiz.html` | Minimal HTML shell with `<div id="quiz-root">` and an inline `<style>` block for vertical stacking. |
| `dashboard/README.md` | This document. |

The default export is the `initQuiz(root, doc)` function (also available
on `window.initQuiz` and `window.sraQuiz.initQuiz` for the end-to-end
driver). The module never reads `correct_option_index` for the question
list — `QuizSetOut` strips that field on the server, and the dashboard
learns correctness only after `POST /answers` returns.

## API endpoints consumed

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/v1/quiz/today` | List today's `QuizSet`s for the signed-in user. |
| `POST` | `/api/v1/quiz/{quiz_set_id}/attempts` | Start a `web` attempt; returns `attempt_id`. |
| `GET`  | `/api/v1/quiz/{quiz_set_id}` | Load the question list (without `correct_option_index`). |
| `POST` | `/api/v1/quiz/attempts/{attempt_id}/answers` | Submit one answer; returns `is_correct`, `correct_option_index`, `explanation`. |
| `POST` | `/api/v1/quiz/attempts/{attempt_id}/complete` | Finalize the attempt; returns `score`, `total_questions`. |
| `GET`  | `/api/v1/quiz/attempts/{attempt_id}` | Read the persisted attempt summary. |

All requests use `credentials: "same-origin"` so the session cookie set by
the Google OAuth flow in [`src/sra/auth/google_oauth.py`](../src/sra/auth/google_oauth.py)
is included automatically.

## Auth model

The dashboard authenticates with the **session cookie** only. The
cookie is issued during the Google OAuth sign-in flow (Phase 1/2/3) and
is sent automatically by the browser for same-origin requests — the
module never reads or stores a bearer token.

`Authorization: Bearer <token>` headers are reserved for the **desktop
client** (and the end-to-end tests, which sign their own tokens). The
dashboard never sets `Authorization`. The server-side `require_user`
dependency enforces ownership, so cross-user data is unreachable from
the client.

## Status-code handling

| Status | Dashboard behavior |
|---|---|
| `200`/`201` | Render the next state (list, question, answer result, summary). |
| `400` | Render a "Cannot start quiz (400)" panel with the server's `detail`. |
| `401` | Render a "Not authenticated (401)" panel; the user must sign in. |
| `403` | Render a "Forbidden (403)" panel; the resource belongs to another user. |
| `404` | Render a "Not found (404)" panel; the quiz or attempt does not exist. |
| `409` | Render a "Request failed (409)" panel (attempt already completed). |
| `429` | Render a "Rate limit exceeded (429)" panel; the `Retry-After` header is surfaced when present. |
| Network failure | Render a "Network error" panel; the exception message is included. |

## Local development

From the repository root, serve the static folder with Python's built-in
HTTP server and open the dashboard in a browser signed in to a user with
at least one pre-generated quiz:

```bash
python -m http.server 8080 --directory dashboard/static
# Open http://localhost:8080/quiz.html in a browser.
```

The dashboard is read-and-call only: it fetches from the FastAPI app
running behind the same origin (or proxied via the local dev server) and
stores no progress in `localStorage`. Refreshes mid-quiz discard the
in-progress attempt; this is intentional for Phase 4.
