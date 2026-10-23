# SH-001 — Coverage matrix and pass-1 failure classification

**Status:** closed

## Bug

Pass 1 of the self-heal hit `pytest -q` with **25 failures, 10 ruff errors, 16 pyright errors** and no way to tell a real application bug from a documented limitation or in-progress work. Root cause: no inventory existed of which routes the suite actually reaches and what "expected behavior" each test encodes, so every failure looked equally actionable and the same failures kept being re-triaged on every run. This ticket produces that inventory and classifies every failure once.

Run on **2026-10-16** against branch `master` @ `fd40949` (SRA-306 "feat(auth): implement automatic application credential refresh", 2026-10-15).

## Files

| File | Change | Why |
|---|---|---|
| `guides/self_heal.md` | edit (read only) | §2 mandates using the repository's existing runner; §4.3 defines the classification scheme applied below. |
| `tests/integration/test_route_policy_audit.py` | edit (read only) | The mounted-route inventory that SH-002 extends. |
| `tests/unit/test_digest_scheduler.py` | edit (read only) | The 8 failures SH-003 addresses. |
| `tests/unit/test_dev_pregenerate_route.py` | edit (read only) | Type-bound check behind SH-004. |
| `tests/unit/test_auth_client.py` | edit (read only) | The fake behind SH-005. |

## Environment

| Item | Value |
|---|---|
| Python / pytest | 3.13.12 (`.venv`) / pytest 9.1.1 |
| Total tests | 988 collected (962 passed, 25 failed, 26 skipped) |
| Ruff / Pyright | 10 errors / 16 errors |

## Repeating test harness

The existing FastAPI `TestClient` + `pytest` setup is the harness. Env vars and credentials come from the existing `.env` / `.env.example`. Per `CONTRIBUTING.md` the dev extra is `'.[dev]'`, the desktop extra `'.[dev,desktop]'`, and live OpenAI is `make test-live`. No new Postman collection is needed — the existing suite already hits the API surface.

```bash
source .venv/bin/activate
pytest -q                       # default suite, in-process HTTP
pytest tests/integration        # cross-route isolation, route-policy audit
pytest tests/unit               # one-component contracts
pytest tests/e2e                # real FastAPI app + real DB + real graphs (no LLM)
make test-data                  # cached-Whisper real-audio flow (opt-in)
make test-live                  # real OpenAI (opt-in)
ruff check .                    # lint
pyright                         # type check
```

## API surface covered

| Route | Method | Auth | Source of expected behavior |
|---|---|---|---|
| `/api/health` | GET | public | `src/sra/api/app.py` |
| `/api/v1/auth/google/login` | GET | anonymous | README §"Configure Google sign-in" |
| `/api/v1/auth/google/callback` | GET | anonymous | `sra.auth.google_oauth` |
| `/api/v1/auth/desktop/exchange` | POST | anonymous | SRA-303 |
| `/api/v1/auth/logout` | POST | anonymous | SRA-302 |
| `/api/v1/auth/me` | GET | user | SRA-302 |
| `/api/v1/auth/refresh` | POST | anonymous | SRA-306 |
| `/api/v1/entries/ingest` | POST | user | Phase 1 (SRA-105) |
| `/api/v1/search` | GET | user | Phase 1 (SRA-104) |
| `/api/v1/digest` | GET | user | Phase 1 (SRA-104) |
| `/api/v1/digest/ready` | GET | user | SRA-502 |
| `/api/v1/chat` | POST | user | Phase 1 (SRA-106) |
| `/api/v1/reviews/upcoming` | GET | user | SRA-701 (WIP) |
| `/api/v1/quiz/today` | GET | user | SRA-404 |
| `/api/v1/quiz/{quiz_set_id}` | GET | user | SRA-404 |
| `/api/v1/quiz/{quiz_set_id}/attempts` | POST | user | SRA-404 |
| `/api/v1/quiz/attempts/{attempt_id}/answers` | POST | user | SRA-404 |
| `/api/v1/quiz/attempts/{attempt_id}/voice-turn` | POST | user | SRA-505 |
| `/api/v1/quiz/attempts/{attempt_id}/complete` | POST | user | SRA-404 |
| `/api/v1/quiz/attempts/{attempt_id}` | GET | user | SRA-404 |
| `/api/v1/quiz/attempts/{attempt_id}/interview-turn` | POST | user | SRA-805 (WIP) |
| `/api/v1/quiz/attempts/{attempt_id}/interview` | GET | user | SRA-805 (WIP) |
| `/api/v1/me/preferences` | GET/PUT | user | SRA-501 |
| `/api/v1/me/dev/pregenerate-now` | POST | user | SRA-701 dev affordance (WIP) |
| `/api/v1/admin/usage` | GET | admin | SRA-302 |
| `/api/v1/admin/rate-limits` | GET | admin | SRA-304 |
| `/api/v1/admin/demo/reset` | POST | admin | SRA-603 |

## Coverage matrix

| Feature | Expected-behavior source | Tests | Status |
|---|---|---|---|
| `/api/health` | `tests/unit/test_app_health.py` | many | passed |
| Auth — refresh (one-shot 401 retry, reuse detection, cookie clear) | `tests/unit/test_api_client_refresh.py`, `tests/integration/test_auth_refresh_route.py` | 27 | passed |
| `/entries/ingest` (shape, ownership, 6 schedules) | `tests/unit/test_ingest_route.py` | 33 | passed |
| `/search` (ownership / pagination) | `tests/unit/test_search_route.py` | many | passed |
| `/digest` ("due today" only) | `tests/unit/test_digest_route.py` | many | passed |
| `/quiz/*` (rate-limit inheritance, RBAC, validation) | `tests/integration/test_quiz_*.py` | many | passed |
| Token-bucket rate limit (capacity / refill / clock / RBAC) | `tests/unit/test_rate_limit.py`, `tests/integration/test_rate_limited_routes.py` | 34 | passed |
| Route-policy audit | `tests/integration/test_route_policy_audit.py` | 1 | **FAILED — SH-002** |
| Scheduler — wakeup logic (local time, idempotency, bad-tz skip) | `tests/unit/test_digest_scheduler.py` | 10 | **8 FAILED — SH-003** |
| Dev pregen route (drops interview counts on `include_upcoming`) | `tests/unit/test_dev_pregenerate_route.py` | type-only | **DEFECT — SH-004** |
| Credential store — refresh-pair persistence | `tests/unit/test_auth_client.py` (pyright) | — | **DEFECT — SH-005** |
| Capture → entries → search → digest → chat (no LLM) | `tests/e2e/*` | many | passed |
| Phase 4 dashboard unit (Node harness) | `tests/unit/test_quiz_dashboard.py` | 8 | **DOCUMENTED LIMITATION (SRA-405)** — harness hard-codes `PATH = "/usr/bin:/usr/local/bin"`; this machine has `node` at `/opt/homebrew/bin/node`; async-DOM timing also documented. |
| Phase 4 e2e + Phase 5 wakeup caplog | `tests/e2e/test_quiz_web_e2e.py` (1), `tests/unit/test_wakeup.py` (1) | 2 | **DOCUMENTED LIMITATION** — SRA-404 (test seeds an entry without `create_entry`) and SRA-509 (caplog test-isolation; passes via explicit-file-list invocation). |
| Phase 8 — interview planner / conversation | `tests/unit/test_interview_planner.py`, `tests/integration/test_phase8_*.py` | ~50 | **IN-PHASE-WIP** — Phase 8 migrations not yet merged into the working chain (SRA-804 not started; SRA-806/807/808/809 planned); fixtures reference an in-progress schema. |
| Playwright user-flow | `tests/playwright/*` | ~5 | **IN-PHASE-WIP** — Phase 7 dashboard in working tree; depends on routes not yet wired. Skipped at module level. |

## Failure classification (per `guides/self_heal.md` §4.3)

| Count | Category |
|---|---|
| **4** | Confirmed application bug — fixed ([SH-002](SH-002-route-audit-refresh.md), [SH-003](SH-003-scheduler-test-time-bomb.md), [SH-004](SH-004-dev-py-pregensummary-fields.md), [SH-005](SH-005-memory-store-save-pair.md)) |
| **~10** | Test or expectation defect / WIP — Phase 8 + Phase 7 dashboard tests + `init_db` / `phase3_migration` fixtures require SRA-804+ migrations not yet in the working tree |
| **~11** | Documented limitation — Phase 4 dashboard PATH/async-DOM (SRA-405), Phase 4 e2e (SRA-404), Phase 5 wakeup caplog quirk (SRA-509) |

The default suite reaches all `user` and `admin` mounted routes through the client wrappers in `tests/e2e/` and the explicit integration tests, so no additional Postman scenarios are needed.

## Open gaps

- Phase 8 real-OpenAI planner, conversation, and review-plan routes — SRA-802 through SRA-809 are not yet in the committed chain.
- Physical microphone, macOS menu-bar hotkey, Google OAuth, and Keychain acceptance — manual checks, documented in [`tests/README.md`](../../tests/README.md).
- Live OpenAI extraction / chat (`make test-live`) — opted in, charges apply, requires `OPENAI_API_KEY`.

Outcome: [`SH-REPORT.md`](SH-REPORT.md).

---

Design context: [`technical-design.md`](../../docs/design/technical-design.md). Process:
[`guides/self_heal.md`](../../guides/self_heal.md). Phase status: [`PROGRESS.md`](../../PROGRESS.md).