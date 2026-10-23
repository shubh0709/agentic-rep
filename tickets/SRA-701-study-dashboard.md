# SRA-701 — Study dashboard

**Phase:** 7 — Study dashboard (entries + search + upcoming reviews) · **Status:** implemented — 2026-10-15

## Summary

Ship one authenticated `/dashboard` page where a signed-in user browses captured study entries, filters by topic/subtopic/tag/summary, expands an entry to read its original transcript, and sees a day-grouped review timeline for the next 7/14/30/90 days. It is a thin SSR shell: a new vanilla JavaScript module drives the data layer through `/api/v1/search`, `/api/v1/digest`, and one new `/api/v1/reviews/upcoming` endpoint. It is read-only and in-memory, and every read passes through `require_user` and a user-scoped helper, so a second user sees only their own data.

## Unlocks

- A signed-in user opens `http://localhost:8000/dashboard`: entry counts, today's due reviews, a 30-day upcoming timeline, expandable raw transcripts, and relative day labels.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/api/schemas.py`](../src/sra/api/schemas.py) | edit | Required `raw_transcript: str` on `EntryOut`; new `ReviewDayOut` (`{date, reviews: list[ReviewOut]}`). |
| [`src/sra/db/repository.py`](../src/sra/db/repository.py) | edit | `list_upcoming_reviews(user_id, from_date, to_date, session)` (`status == "pending"`), `count_user_entries(user_id, session)`. Intervals (1d, 3d, 7d, 1mo, 3mo, 6mo) come from [`src/sra/db/scheduler.py`](../src/sra/db/scheduler.py). |
| [`src/sra/api/routers/reviews.py`](../src/sra/api/routers/reviews.py) | new | `GET /api/v1/reviews/upcoming?days=N` (1–365, default 30); window `(today, today + days]`; omits empty days. |
| [`src/sra/api/routers/__init__.py`](../src/sra/api/routers/__init__.py) | edit | Mounts `reviews.router`; inherits `limit_authenticated`. |
| [`src/sra/api/routers/search.py`](../src/sra/api/routers/search.py), [`digest.py`](../src/sra/api/routers/digest.py), [`entries.py`](../src/sra/api/routers/entries.py), [`quiz.py`](../src/sra/api/routers/quiz.py) | edit | Each propagates `EntryOut.raw_transcript`. |
| [`web/routes.py`](../web/routes.py) | edit | `GET /dashboard`; same auth pattern as `/quiz` and `/settings`. |
| [`web/templates/dashboard.html`](../web/templates/dashboard.html) | new | Extends `base.html`; ids `stat-total-entries`, `stat-due-today`, `stat-upcoming-total`, `entry-search`, `upcoming-window`, `entries-root`, `upcoming-root`. |
| [`web/templates/_nav.html`](../web/templates/_nav.html), [`landing.html`](../web/templates/landing.html) | edit | `Dashboard` nav link (`{% if active_nav == 'dashboard' %}is-active{% endif %}`); landing CTA once signed in. |
| [`web/static/js/dashboard.js`](../web/static/js/dashboard.js) | new | ES module: `/api/v1/search?q=…`, `/api/v1/digest`, `/api/v1/reviews/upcoming?days=N`, `credentials: "same-origin"`; 200 ms debounce; 401 → `/signin?next=/dashboard`. |
| [`web/static/css/site.css`](../web/static/css/site.css) | edit | Adds `.dashboard-card`, `.stats-row`, `.stat-tile`, `.entry-card`, `.entry-tags`, `.entry-tag`, `.entry-transcript`, `.upcoming-day`, `.upcoming-day-header`, `.upcoming-review`, `.upcoming-review-interval`; reuses `:root`, `.panel`, `.button`, `.muted`, `.error`. |
| [`tests/unit/test_repository.py`](../tests/unit/test_repository.py), [`tests/unit/test_ingest_route.py`](../tests/unit/test_ingest_route.py) | edit | `list_upcoming_reviews` scoping/ownership/completed rows; `count_user_entries` zero/populated/scoped; `EntryOut` keys add `raw_transcript`. |
| [`tests/unit/test_reviews_routes.py`](../tests/unit/test_reviews_routes.py) | new | Bucketing, ownership, `days` bounds, auth, rate limit, empty days. `phase7` marker. |
| [`tests/integration/test_web_pages.py`](../tests/integration/test_web_pages.py) | edit | `/dashboard` auth, SSR shell, static asset, nav link. |
| [`tests/integration/test_route_policy_audit.py`](../tests/integration/test_route_policy_audit.py) | edit | Audit covers `/api/v1/reviews/upcoming` and `/dashboard`. |
| [`tests/e2e/test_dashboard_e2e.py`](../tests/e2e/test_dashboard_e2e.py) | new | Same three-endpoint sequence as the JavaScript. `phase7` marker. |
| [`pyproject.toml`](../pyproject.toml) | edit | Registers `phase7` and `phase6` markers. |
| [`tickets/README.md`](../tickets/README.md) | edit | Phase 7 board entry for SRA-701. |

## Acceptance criteria

- [x] `GET /dashboard` renders the SSR shell for an authenticated user
      and returns 401 without a session.
- [x] `GET /api/v1/reviews/upcoming` returns pending reviews bucketed by
      `scheduled_date`, strictly after today, omitting empty days.
- [x] The new endpoint rejects `days < 1` and `days > 365` with 422 and
      inherits the authenticated rate-limit policy.
- [x] `EntryOut.raw_transcript` is required and present on every response
      that carries an entry — search, digest, entries/ingest, quiz.
- [x] The search box debounces input by 200 ms and uses
      `/api/v1/search?q=...`; clearing the box restores the full list.
- [x] The dashboard's "Due today" tile reads from `/api/v1/digest`; the
      "Upcoming (next 30 days)" tile reads from
      `/api/v1/reviews/upcoming?days=30`.
- [x] Cross-user isolation: a second user's token never sees the first
      user's entries or reviews in any of the three data endpoints
      (asserted by `test_upcoming_is_isolated_per_user` and the same
      coverage already on `/search` and `/digest`).
- [x] The route policy audit covers `/api/v1/reviews/upcoming` and
      `/dashboard`.
- [x] Lint (`ruff check .`) and type check (`pyright` on changed files)
      pass with zero new warnings.
- [x] Focused, integration, and e2e tests for this ticket pass; pre-
      existing Phase 4/5/6 failures are documented and not caused by this
      ticket.

## Tests

```bash
pytest tests/unit/test_reviews_routes.py tests/unit/test_repository.py -v
pytest tests/integration/test_web_pages.py -v
pytest tests/e2e/test_dashboard_e2e.py -v
pytest -m phase7 -q
```

`phase7` marks `tests/unit/test_reviews_routes.py` and `tests/e2e/test_dashboard_e2e.py`, so the last command selects this ticket's 17 tests. Lint, type check, and the full command list: [`tests/README.md`](../tests/README.md).

## Manual verification

- [ ] `uvicorn sra.api.app:create_app --factory --reload --port 8000`; `python -m sra.capture.menubar_app` in a second terminal to ingest notes.
- [ ] The three tiles in `http://localhost:8000/dashboard` match `curl` calls to `/api/v1/search`, `/api/v1/digest`, and `/api/v1/reviews/upcoming?days=30`.
- [ ] Typing narrows the search list within ~200 ms; clearing it restores the full list; "Show original transcript" reveals the text exactly as sent to `/api/v1/entries/ingest`.
- [ ] Switching the "Next N days" `<select>` re-renders the timeline without a reload; labels update across the 7/14/30/90 boundaries.
- [ ] A private-window sign-in as a second user shows only that user's entries.

**Verification:**

- **Date and environment:** 2026-10-15, macOS, Python 3.13, FastAPI + SQLite.
- **Focused:** `pytest tests/unit/test_reviews_routes.py tests/unit/test_repository.py tests/integration/test_web_pages.py tests/e2e/test_dashboard_e2e.py -v` — **36 passed**; `ruff check .` all checks passed; `pyright` on the changed files — **0 errors, 0 warnings**.
- **Full suite:** `pytest -q` — **773 passed, 14 skipped, 23 failed**; all 23 pre-existing on `master` (`git stash`): `tests/unit/test_quiz_routes.py` (1), `tests/integration/test_phase4_end_to_end.py` (3), `tests/unit/test_quiz_dashboard.py` (7, hard-coded `PATH` misses `node`), `tests/unit/test_digest_scheduler.py` (11), `tests/unit/test_wakeup.py` (1), `tests/e2e/test_phase4_web_quiz_e2e.py`, `tests/e2e/test_quiz_web_e2e.py`, `tests/integration/test_phase3_migration.py` (3).
- **Artifacts:** no new rows or services; reads are scoped to `user_id` over indexed `study_entries`/`review_schedules` columns.
- **Known caveat:** browser verification is pending a real-capture acceptance run; the e2e and integration tests cover the data shape and the JavaScript fetch sequence. No "mark review done" affordance yet — a later ticket.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
