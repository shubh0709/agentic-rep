# SRA-1203 — Topic-card CRUD and direct quiz launch

**Type:** Feature
**Phase:** 12 — Calm experience and motivation
**Priority:** High — turns the Library from a read-only shelf into a usable study workspace
**Status:** Implemented; automated verification complete
**Blocked by:** SRA-1202, SRA-404
**Blocks:** Future topic merge/split controls

## Summary

Give the Today dashboard more breathing room and make every topic card actionable.
Clicking a card opens an accessible edit dialog for its underlying notes, where a
learner can change a note's topic and capture date or delete it. A separate
`Quiz me` action starts that note's available quiz immediately. The dashboard's
global `Interview Me` label also becomes `Quiz me` and continues to open the
on-demand assessment flow.

## Where things stand right now

The SRA-1202 shelf groups entries by normalized topic name and exposes read-only
note drawers. Entry creation exists, but the entries API has no update or delete
contract. The ordinary quiz page lists every quiz generated today and cannot
honor a requested quiz-set ID, so a topic card cannot currently launch its own
quiz. Dashboard sections use a compact 24px vertical margin.

## What this ticket unlocks

A learner can click the “Check” card, edit its topic to “Figure out job
crawling,” move its capture date, save, and see grouping and schedules refresh.
They can delete an obsolete note with confirmation, or choose `Quiz me` to begin
the latest generated quiz for that card without first selecting it from a list.

## How this fits into the whole app

```mermaid
flowchart LR
    A[Click topic card] --> B[Edit dialog]
    B --> C{Save or delete}
    C -->|PATCH entry| D[Owner-scoped entry service]
    C -->|DELETE entry| D
    D --> E[SQLite entry and dependent rows]
    E --> F[Reload Library, counts, and review horizon]
    A -->|Quiz me button| G[Resolve newest quiz set for card entries]
    G --> H[/quiz?quiz_set_id=ID]
    H --> I[Start that web quiz immediately]
```

The card click and its nested controls are deliberately separate interactions:
the card opens the editor, while `Quiz me`, note disclosure, and regeneration
stop propagation. Updates and deletes are owner-scoped in the API. Deletion
removes dependent review, reference, quiz, attempt, answer, interview, and
assessment data through the repository's explicit cascade behavior before the
entry itself is removed.

## Files and why they matter

| File | Responsibility |
|---|---|
| `src/sra/api/schemas.py` | Defines the strict partial entry-update payload. |
| `src/sra/db/repository.py` | Applies owner-scoped updates and safe dependent-row deletion. |
| `src/sra/api/routers/entries.py` | Exposes authenticated `PATCH` and `DELETE` entry routes. |
| `web/static/js/dashboard.js` | Adds clickable cards, edit/delete dialog, refreshed state, and per-card quiz links. |
| `web/static/js/quiz.js` | Honors `quiz_set_id` and immediately starts the requested quiz. |
| `web/templates/dashboard.html`, `web/templates/_nav.html`, `web/templates/interview_me.html` | Renames the visible assessment action to `Quiz me` while retaining existing routes. |
| `web/static/css/site.css` | Increases section spacing and styles interactive-card and dialog states. |
| `tests/unit/`, `tests/e2e/`, `tests/playwright/` | Pin validation, tenancy, persistence/cascade, direct launch, keyboard, and browser behavior. |

## TDD starting point

A test-only subagent adds failing API unit/e2e contracts plus browser-facing
source or Playwright contracts before production changes. External inference is
not needed: seeded entries and quiz rows keep FastAPI, authentication, SQLModel,
SQLite, routing, and browser JavaScript real.

## What to implement

- Accept a strict partial update containing at least one of `topic` or
  `created_at`; trim and require a non-empty topic; reject unknown fields.
- Return `404` for a missing entry and `403` for another user's entry, without
  revealing or mutating it.
- When `created_at` changes, preserve each review's interval relationship by
  recomputing pending schedule dates from the new capture date.
- Delete the owned entry and every dependent row safely; return `204`.
- Make cards keyboard/click operable and open a modal with note-level edit and
  delete controls. Escape, backdrop, cancel, and focus containment must work.
- Reload dashboard data after mutations so grouping, counts, dates, and horizon
  remain truthful.
- Show `Quiz me` only when a card has a quiz set, choose the newest eligible set,
  and launch `/quiz?quiz_set_id=<id>` immediately. A missing or inaccessible ID
  produces a useful quiz-page error rather than falling back silently.
- Increase vertical spacing between the major Today sections at desktop and
  mobile widths without creating horizontal overflow.
- Rename visible `Interview Me` navigation/dashboard/page labels to `Quiz me`;
  URLs and internal identifiers remain compatible.

## Acceptance criteria

- [x] Major Today sections have visibly larger, responsive vertical separation.
- [x] Pointer and keyboard activation of a topic card opens an accessible editor; nested controls keep their own actions.
- [x] A learner can update topic and capture date, and the refreshed card/review schedule reflects the changes.
- [x] A learner can delete an owned note after confirmation; dependent data is removed and the dashboard refreshes.
- [x] Cross-user update/delete attempts fail without mutation.
- [x] A card's `Quiz me` starts that topic's available quiz immediately.
- [x] Visible `Interview Me` labels become `Quiz me` without breaking the existing assessment route.

## How to test this ticket alone

```bash
pytest tests/unit/test_entry_crud.py tests/e2e/test_topic_card_crud_e2e.py -q
SRA_RUN_PLAYWRIGHT_TESTS=1 pytest tests/playwright/test_user_flows.py -q
node --check web/static/js/dashboard.js
node --check web/static/js/quiz.js
ruff check .
pyright
pytest -q
```

## Manual verification

Seed topics named “Check” and “Figure out job crawling,” with one generated quiz.
At desktop and 375px widths, verify section spacing, card click/keyboard editing,
date rescheduling, confirmed deletion, and immediate topic-specific quiz launch.
Verify cancel/Escape leaves data unchanged and another user's entry cannot be
edited through a direct API request.

## Verification record

- Date and environment: 2026-10-24, macOS arm64, Python 3.13, Node, headless Chromium.
- Commands and inputs: focused SRA-1203 unit/e2e tests, route-policy audit, full non-conflicting regression suite, Ruff, Pyright, Node syntax, and the real-browser topic-create/card-action flow.
- New IDs/artifacts and observed downstream results: an owned “Check” entry was renamed to “Figure out job crawling,” its six pending dates moved with the edited capture date, search returned the new topic, and deletion removed its review/reference/quiz/question/attempt graph. The generated-topic card linked `Quiz me` to the same concrete quiz-set ID as its interview action.
- Failure/recovery and cleanup: empty/unknown update fields returned 422, cross-owner mutations returned 403, missing IDs returned 404, and anonymous mutations returned 401. Browser/database fixtures owned cleanup.
- Automated/manual results, skips, and limitations: focused + route audit **12 passed**; full non-conflicting suite **1269 passed, 48 skipped, 5 deselected**; browser suite **33 passed** before the intentional label assertion was updated, then the affected flow passed on rerun; Ruff/Pyright/Node clean. Two deselected tests need loopback sockets unavailable in the sandbox, and three belong to unrelated in-progress SRA-1204 timezone/orientation changes already in the worktree. No separate manual browser acceptance was recorded.

## Definition of Done

- [x] Acceptance criteria met and actual evidence recorded
- [x] Focused, browser, regression, lint, type, and JavaScript checks complete
- [x] Ticket board and progress documentation match verified behavior
