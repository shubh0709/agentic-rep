# SRA-902 — Dashboard "Create topic with references" UI + generate-now endpoint

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** not started

## Summary

Adds the dashboard affordance the user asked for: a button to create a new topic (picked or typed), attach references (URLs, pasted text, uploads), click "Generate now", and immediately see quiz + interview cards generated from the attached source material. Also wires `POST /api/v1/topics/{entry_id}/regenerate` to a real handler that re-runs the quiz + interview generation against the entry's references — SRA-901 ships that endpoint as a `501` stub until SRA-903 / SRA-904 land. Vanilla DOM only, matching the existing `web/static/js/` modules.

## Unlocks

- SRA-903 (quiz uplift) and SRA-904 (interview uplift) gain a user-visible path that exercises their reference-aware generators.
- A user can add material to an existing topic and regenerate without losing their review schedule.

## Files

| File | Change | Why |
|---|---|---|
| `web/templates/dashboard.html` | edit | A "Create topic" button at the top-right of the entries section; per-entry "Add references" / "Regenerate" actions. |
| `web/static/js/dashboard.js` | edit | Modal controller, references list editor, submit, regenerate wiring, post-create render. |
| `web/static/css/site.css` | edit | Modal + reference list + "Generating…" spinner styling. |
| `src/sra/api/routers/topics.py` | edit | `regenerate_topic` handler (was a `501` stub in SRA-901) — runs `quiz_graph.generate_and_persist_questions` + `interview_planner.generate_interview_outline` per entry's references. |
| `src/sra/api/schemas.py` | edit | `TopicRegenerateResult`, same shape as `TopicCreateResult`. |
| `tests/unit/test_dashboard_phase9.py` | new | DOM helpers for modal open/close, references list edit, button states. |
| `tests/integration/test_phase9_regenerate.py` | new | Re-running generation on an existing entry replaces `QuizSet` + `InterviewPlan` without double-charging the review schedule. |
| `tests/e2e/test_phase9_dashboard_create_topic_e2e.py` | new | Click-create-fill-submit flow with `fetch` mocked. |
| `README.md` | edit | A "Creating a topic" walkthrough section. |

## Design notes

- **Modal inputs.** Topic (required, 1–200 chars after strip), Subtopic (optional), Tags (optional, comma-separated — the server splits), Summary (optional, ≤ 2,000 chars), and a References list (max 10 per submission) with a kind selector (`url` | `paste` | `upload`), a Title (required, 1–200 chars), one of URL input / pasted-text area / file input, and a per-row "Remove". For `upload`, the browser uses `<input type="file" accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown">` and `FileReader.readAsDataURL(...)` base64-encodes the bytes client-side, then posts `kind: "upload", file_name, content_type, file_b64` — mirroring SRA-901's JSON contract.
- **In-flight state.** The submit button is disabled and shows a "Generating…" spinner while the POST is in flight. On a 4xx, `errors[]` is surfaced inline per reference (e.g. "couldn't fetch https://…: timeout") with a per-row "Retry" affordance.
- **`regenerate_topic(entry_id, body, user, session)`** returns 404 on a missing entry, 403 cross-user, 422 when there are no references. It loads references through `repository.list_references_for_entry`, delegates to a private `_generate_for_entry(entry, references, user, session)` that calls both generators (SRA-903 / SRA-904 implement the reference-aware variants), and returns `TopicRegenerateResult`. It **never mutates `ReviewSchedule`**, mirroring the SRA-509 dev-affordance invariant.

## Acceptance criteria

- [ ] "Create topic" modal opens, accepts the documented input shape, and submits the payload defined in SRA-901
- [ ] After a successful POST the new entry card appears inline with quiz + interview links driven by `quiz_set_id` / `interview_plan_id`
- [ ] Per-reference fetch errors are surfaced inline with retry per row
- [ ] `regenerate` endpoint replaces the existing `QuizSet` + `InterviewPlan` (idempotent within a day) and preserves the user's review schedule
- [ ] Cross-user isolation: user B hitting `POST /api/v1/topics/{A's entry}/regenerate` gets 403

## Tests

```bash
pytest tests/unit/test_dashboard_phase9.py tests/integration/test_phase9_regenerate.py -v
pytest tests/e2e/test_phase9_dashboard_create_topic_e2e.py -v
pytest -m 'not phase9' -q
ruff check web
pyright web
```

`src/sra/api/routers/dev.py::dev_pregenerate_now` (Phase 5) is the closest precedent for the typed-summary-plus-partial-failure response shape.

## Manual verification

- [ ] Sign in via `/api/v1/auth/google/login`, open `/dashboard`, click **Create topic**, fill in a topic + 1 URL + 1 paste, click **Generate now**. Confirm the new card appears inline with quiz/interview links; click **Start quiz** and complete the quiz once.
- [ ] On an older entry card, click **Add references**, paste a new snippet, click **Regenerate**, and confirm a new quiz set appears under the entry card.
- [ ] Open a private window signed in as a second user; the first user's topics must not be visible.

> **Note:** the verification record for this ticket is unfilled — no actual run has been recorded.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).