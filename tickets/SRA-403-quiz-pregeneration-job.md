# SRA-403 — Quiz pregeneration job

**Phase:** 4 — Daily MCQ quiz (web) · **Status:** not started

## Summary

Add the nightly batch job that, for each user, walks every entry due today and
generates one `QuizSet` per entry that does not already have one. The job is
**idempotent**: running it twice in one day never creates a second `QuizSet` for the
same entry. It enumerates due entries through the existing
`repository.get_due_reviews`, so "an entry is due today" has exactly one definition
across the app. This ticket does **not** schedule the job — APScheduler wiring and
the `preferred_review_time` window are Phase 5 (SRA-502) — and does **not** add an
HTTP route, which is SRA-404.

## Unlocks

- `pregenerate_quizzes_for_day(today=..., session=...)` returns a counts dict such as `{'users': 2, 'entries_seen': 3, 'sets_created': 2, 'sets_skipped': 1, 'errors': 0}`.
- SRA-404's `GET /api/v1/quiz/today` always has pre-generated rows to serve, so no dashboard open waits on a model call.
- SRA-502 can schedule the same function without changing call sites.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/jobs/__init__.py` | new | Empty package marker; Phase 5 adds `digest_scheduler.py` and Phase 6 `demo_reset.py` beside `quiz_pregeneration.py`. |
| `src/sra/jobs/quiz_pregeneration.py` | new | `PregenerationSummary` TypedDict (`users`, `entries_seen`, `sets_created`, `sets_skipped`, `errors`); `pregenerate_quizzes_for_day(*, today, session)`; `pregenerate_quizzes_for_user(*, user, today, session)`; `_resolve_user_quiz_preferences(user) -> tuple[int, int]`. |
| `src/sra/db/repository.py` | edit | Reuse `get_due_reviews` rather than re-implementing the join — that shared call is the contract keeping the digest's view of "due today" and the job's view identical. Confirm `has_quiz_set_for_entry_today` exists (SRA-402) and add it here if it is missing. |
| `src/sra/agents/quiz_graph.py` | edit (use) | Call `generate_and_persist_questions(entry, user_id, session, question_count, option_count)` and catch `QuizGenerationError` so one entry's failure does not stop the batch. |
| `tests/unit/test_quiz_pregeneration.py` | new | Create / skip / two-due-entries / error-containment / run-twice / not-due-today / `pregenerate_quizzes_for_user` in isolation, with `sra.agents.quiz_graph._get_chat_model` monkeypatched. |
| `tests/integration/test_quiz_pregeneration_e2e.py` | new | Temporary SQLite file, two users, three entries (one not due, two due, one already has a set); assert the `(users, sets_created, sets_skipped)` summary and the quiz rows. Marked `@pytest.mark.phase4`; skips cleanly without a writable temp directory. |
| `src/sra/api/app.py` | read only | The job is not started by `create_app()`; scheduler wiring is Phase 5. |

The job must not start an APScheduler thread, open an HTTP server, or call OpenAI
itself — every model invocation goes through `_get_chat_model()`.

> **Note:** `quiz_question_count` / `quiz_option_count` are not columns on `users`
> yet. `_resolve_user_quiz_preferences` returns the design defaults `(10, 4)` today
> and exists purely as the seam SRA-501 replaces without moving call sites.

## Acceptance criteria

- [ ] An entry with no `QuizSet` for today gets one created; an entry with an existing `QuizSet` for today is skipped
- [ ] Two due entries for one user both get sets; an entry not due today is ignored
- [ ] Running the job twice the same day produces `sets_created=0` on the second run and never duplicates or overwrites
- [ ] An LLM failure for one entry increments `errors`, leaves that entry's row count unchanged, and does not stop the next user
- [ ] `pregenerate_quizzes_for_user` works in isolation
- [ ] The job starts no APScheduler thread, opens no HTTP server, and requires no OpenAI key
- [ ] Phase 1/2/3 regressions remain green; Pyright and Ruff pass without new warnings

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_pregeneration.py -v
pytest tests/integration/test_quiz_pregeneration_e2e.py -v
pytest -m 'not phase4' -q
ruff check .
pyright
```

`_get_chat_model` is monkeypatched on `sra.agents.quiz_graph`, so the integration
test needs no OpenAI key and no HTTP server.

## Manual verification

- [ ] Seed two users, then call `pregenerate_quizzes_for_day(today=date.today(), session=session)` against a real database with a configured `OPENAI_API_KEY`
- [ ] Record the summary dict: users walked, due entries, sets created, sets skipped, errors
- [ ] Confirm one `QuizSet` row per due entry on the first run and none added on the second run
- [ ] Inspect one set's `quiz_questions` for a sensible `order_index`, `correct_option_index`, and option count
- [ ] Do not use a production database for the first run

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).