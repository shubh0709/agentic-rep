# SRA-902 — Dashboard "Create topic with references" UI + generate-now endpoint

**Type:** Implementation
**Phase:** 9
**Priority:** High
**Status:** Planned
**Blocked by:** SRA-901
**Blocks:** SRA-903 (quiz uplift), SRA-904 (interview uplift)

## Summary

Adds the dashboard affordance the user asked for: a button to create a new topic
(pick or type), attach references (URLs + pasted text), click "Generate now", and
immediately see quiz + interview cards generated from the attached source material.
Also wires `POST /api/v1/topics/{entry_id}/regenerate` to a real handler that
re-runs the quiz + interview generation against the entry's references (the
SRA-901 endpoint returns `501` until SRA-903/SRA-904 land).

## Where things stand right now

- SRA-701 ships `web/templates/dashboard.html` + `web/static/js/dashboard.js`.
- SRA-901 has shipped `POST /api/v1/topics`.
- There is no in-page modal so far; the codebase uses vanilla DOM-only modules
  (`web/static/js/quiz.js`, `dashboard.js`).

## What this ticket unlocks

After SRA-902:

1. Open `/dashboard`, click **Create topic**, type *Transformer attention heads*,
   add an `Add URL` and an `Add pasted text`, click **Generate now**.
2. The modal shows a "Generating…" state and resolves to a new entry card with
   **Start quiz** and **Start interview** actions.
3. From any existing entry card, click **Add references**, paste new snippets,
   submit — a server-side `regenerate` runs and the entry's quiz/interview links
   reflect the new material after the LLM call completes.
4. `POST /api/v1/topics/{id}/regenerate` is reachable from the **Regenerate** button
   that appears on entry cards that already have a quiz set; it now re-runs the
   full generation pipeline and reports partial-success like the modal does.

## How this fits into the whole app

```mermaid
sequenceDiagram
    participant U as Browser
    participant JS as dashboard.js (SRA-902)
    participant T as POST /api/v1/topics
    participant R as POST /api/v1/topics/{id}/regenerate
    participant Q as quiz_graph
    participant I as interview_planner
    U->>JS: Click "Create topic"
    JS->>JS: open modal, render topic/ref inputs
    U->>JS: Fill topic + add URL + add paste
    JS->>T: POST {topic, summary, references[]}
    T-->>JS: 201 {entry, references, quiz_set_id, interview_plan_id, errors}
    JS->>JS: render new card inline; show quiz/interview links
    Note over U,I: "Generate now" UX mirrors the existing dev-mode affordance (SRA-509).
    U->>JS: Click "Regenerate" on existing entry
    JS->>R: POST {id}
    R->>Q: generate_and_persist_questions(references)
    R->>I: generate_interview_outline(references)
    R-->>JS: {quiz_set_id, interview_plan_id, errors}
    JS->>JS: replace card's quiz/interview links
```

## Files and why they matter

| File | Action | Reason |
|---|---|---|
| `web/templates/dashboard.html` | modify | "Create topic" button (top-right of entries section); per-entry "Add references" / "Regenerate" actions |
| `web/static/js/dashboard.js` | modify | Modal controller, references list editor, submit, regenerate wiring, post-create render |
| `web/static/css/site.css` | modify | Modal + reference list + "Generating…" spinner styling |
| `src/sra/api/routers/topics.py` | modify | `regenerate_topic` handler (was a 501 stub in SRA-901) — runs `quiz_graph.generate_and_persist_questions` + `interview_planner.generate_interview_outline` per entry's references |
| `src/sra/api/schemas.py` | modify | `TopicRegenerateResult` (same shape as `TopicCreateResult`) |
| `tests/e2e/test_phase9_dashboard_create_topic_e2e.py` | new | Click-create-fill-submit flow with `fetch` mocked |
| `tests/unit/test_dashboard_phase9.py` | new | DOM helpers for modal open/close, references list edit, button states |
| `tests/integration/test_phase9_regenerate.py` | new | Re-running generation on existing entry replaces `QuizSet` + `InterviewPlan`, doesn't double-charge review schedule |

## TDD starting point

- `src/sra/api/routers/topics.py::regenerate_topic` raises `NotImplementedError`.
- `web/static/js/dashboard.js` has no modal logic; the dashboard renders a search
  input + entries + upcoming reviews.
- `web/templates/dashboard.html` has no `data-action="create-topic"` affordance.

## What to implement

### UI (vanilla DOM, no framework)

- A `data-action="create-topic"` button at the top-right of the entries section
  opens a `<dialog>` (or `<div role="dialog">` with an ARIA-managed focus trap).
- Inputs:
  - **Topic** (required, 1-200 chars after strip)
  - **Subtopic** (optional)
  - **Tags** (optional, comma-separated — server splits)
  - **Summary** (optional, ≤ 2,000 chars)
  - **References** list (max 10 per submission):
    - Kind selector (`url` | `paste` | `upload`)
    - Title (required, 1-200 chars)
    - One of: URL input, pasted text area, or file input
    - "Remove" button per row
    - For `upload`: browser picks file via `<input type="file" accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown">`; `FileReader.readAsDataURL(...)` base64-encodes the bytes client-side; client posts `kind: "upload", file_name, content_type, file_b64` (mirroring the JSON endpoint's contract from SRA-901).
  - **Generate now** submit + **Cancel** button
- POST /api/v1/topics; on 201, close the modal and append a new entry card with
  `quiz_set_id`/`interview_plan_id` rendered as **Start quiz** / **Start interview**
  inline links (links hit the existing `/quiz/{id}` page).
- Submit button disabled + spinner text "Generating…" while the POST is in flight.
- On 4xx error: surface `errors[]` per reference inline (e.g., "couldn't fetch
  https://…: timeout") with a "Retry" affordance per reference.

### Server

- `regenerate_topic(entry_id, body, user, session)`:
  - 404 on missing entry, 403 on cross-user, 422 on no references.
  - Loads references via `repository.list_references_for_entry`.
  - Calls a private helper `_generate_for_entry(entry, references, user, session)`
    which calls both `quiz_graph.generate_and_persist_questions` and
    `interview_planner.generate_interview_outline` (SRA-903/SRA-904 implement the
    reference-aware variants).
  - Returns `TopicRegenerateResult` mirroring `TopicCreateResult`.
  - Honours the natural review schedule — never mutates `ReviewSchedule`
    (mirrors the SRA-509 dev-affordance invariant).

## Where to look

- `dashboard/static/quiz.js` for the existing single-question flow and the
  `panel(kind)` / `el(tag, attrs, text)` helpers.
- `web/static/css/site.css` for the existing token palette + button styles.
- `src/sra/api/routers/dev.py::dev_pregenerate_now` (Phase 5) for the closest
  precedent — the route returns a typed summary with quiz-set IDs and partial
  failures the same way this ticket should.

## Acceptance criteria

- [ ] "Create topic" modal opens, accepts the documented input shape, and submits the
      payload defined in SRA-901.
- [ ] After a successful POST the new entry card appears inline with quiz + interview
      links driven by `quiz_set_id` / `interview_plan_id`.
- [ ] Per-reference fetch errors are surfaced inline with retry per row.
- [ ] `regenerate` endpoint replaces the existing `QuizSet` + `InterviewPlan` (idempotent
      within a day) and preserves the user's review schedule.
- [ ] Cross-user isolation: user B hitting `POST /api/v1/topics/{A's entry}/regenerate`
      gets 403.

## How to test this ticket alone

```bash
pytest tests/unit/test_dashboard_phase9.py tests/integration/test_phase9_regenerate.py -v
pytest tests/e2e/test_phase9_dashboard_create_topic_e2e.py -v
ruff check web
pyright web
pytest -m 'not phase9' -q   # baseline stays green
```

## Manual verification

1. Sign in via `/api/v1/auth/google/login`.
2. Open `/dashboard`, click **Create topic**, fill in a topic + 1 URL + 1 paste,
   click **Generate now**.
3. Confirm the new card appears inline with quiz/interview links; click **Start
   quiz** and complete the quiz once.
4. On an older entry card, click **Add references**, paste a new snippet, click
   **Regenerate**, confirm a new quiz set appears under the entry card.
5. Open a private window signed in as a second user; the first user's topics must
   not be visible.

## Verification record

- Date and environment:
- Commands and inputs:
- New IDs/artifacts and observed downstream results:
- Failure/recovery and cleanup:
- Automated/manual results, skips, and limitations:

## Definition of Done

- [ ] Acceptance criteria met
- [ ] No new lint / type errors
- [ ] Manual flow records a real new entry + quiz + interview
- [ ] README has a "Creating a topic" walkthrough section
- [ ] PROGRESS.md and the Phase 9 board updated
