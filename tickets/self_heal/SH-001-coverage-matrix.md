Application Self-Heal — Coverage Matrix and Pass-1 Failure Classification
========================================================================

Run on **2026-10-16** against branch `master` @ `fd40949` (SRA-306 "feat(auth):
implement automatic application credential refresh" on 2026-10-15).

## Environment

| Item | Value |
|---|---|
| Python | 3.13.12 (`.venv`) |
| pytest | pytest 9.1.1 |
| Total tests | 988 collected (962 passed, 25 failed, 26 skipped) |
| Ruff | 10 errors |
| Pyright | 16 errors |

## Repeating test harness

The repository's existing FastAPI `TestClient` + `pytest` setup is the harness
per `guides/self_heal.md` §2 ("Use the repository's existing runner where
appropriate"). Run with:

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

Environment variables and credentials come from the existing `.env` /
`.env.example`. Per `CONTRIBUTING.md` the development extra is `'.[dev]'`,
the desktop extra is `'.[dev,desktop]'`, and the live OpenAI suite is
`make test-live`. No new Postman collection is added — the existing pytest
suite already covers the API surface.

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

| Feature / endpoint | Scenario | Expected behavior source | Test ID | Status |
|---|---|---|---|---|
| `/api/health` | 200, JSON body | `tests/unit/test_app_health.py` | many | passed |
| Auth — refresh | one-shot 401 retry on ingestion; reuse-detection; cookie clear | `tests/unit/test_api_client_refresh.py` + `tests/integration/test_auth_refresh_route.py` (extant) | 27 tests | passed |
| `/entries/ingest` | shape, ownership, 6 schedules | tests/unit/test_ingest_route.py | 33 | passed |
| `/search` | ownership / pagination | tests/unit/test_search_route.py | many | passed |
| `/digest` | "due today" only | tests/unit/test_digest_route.py | many | passed |
| `/quiz/*` | rate-limit inheritance, RBAC, validation | tests/integration/test_quiz_*.py | many | passed |
| Token-bucket rate limit | capacity / refill / clock / RBAC | `tests/unit/test_rate_limit.py`, `tests/integration/test_rate_limited_routes.py` | 34 | passed |
| Route-policy audit | every mounted route declares (method, path) and policy | `tests/integration/test_route_policy_audit.py` | 1 | **FAILED — see SH-002** |
| Scheduler — wakeup logic | local time conversion, idempotency, bad-tz skip, no-op on next day | `tests/unit/test_digest_scheduler.py` | 10 | **8 FAILED — see SH-003** |
| Dev pregen route | `DEV_MODE=true` runs immediately, drops interview plan counts on `include_upcoming` path | `tests/unit/test_dev_pregenerate_route.py` (exercised through runner, type-bound) | type-only | **DEFECT — see SH-004** |
| Credential store — refresh-pair persistence | `MemoryCredentialStore` matches `CredentialStore` protocol | (pyright) | — | **DEFECT — see SH-005** |
| End-to-end capture → saved entries → search → digest → chat | real FastAPI app, no LLM | tests/e2e/* | many | passed |
| Phase 4 dashboard unit (Node harness) | SPA loads, renders pre-redaction question list | `tests/unit/test_quiz_dashboard.py` | 8 | **DOCUMENTED LIMITATION — PATH `/usr/bin:/usr/local/bin` hard-coded; this machine has `node` at `/opt/homebrew/bin/node`; async-DOM timing documented in SRA-405.** |
| Phase 4 e2e & Phase 5 wakeup caplog | one Phase 4 e2e + caplog isolation quirk | `tests/e2e/test_quiz_web_e2e.py` (1) + `tests/unit/test_wakeup.py` (1) | 2 | **DOCUMENTED LIMITATION — SRA-404 (test seeds entry without `create_entry`) and SRA-509 (caplog test-isolation; passes via explicit-file-list invocation).** |
| Phase 8 — interview planner/conversation | LLM-driven outline, per-turn grading | `tests/unit/test_interview_planner.py`, `tests/integration/test_phase8_*.py` | ~50 | **IN-PHASE-WIP — Phase 8 migrations not yet merged into the working chain (SRA-804 not started; SRA-806/807/808/809 planned). Test fixtures for Phase 8 columns reference an in-progress schema. Failures here mean the in-progress Phase 8 work needs those migrations before it can pass.** |
| Playwright user-flow | browser-driven dashboard + interview e2e | `tests/playwright/*` | ~5 | **IN-PHASE-WIP — Phase 7 dashboard implementation in working tree; the playwright directory is a sibling fixture that depends on routes not yet wired. Skipped at module level.** |

## Failure classification (per `guides/self_heal.md` §4.3)

| Failure count | Category | Notes |
|---|---|---|
| **4** | **Confirmed application bug — fixed** | SH-002, SH-003, SH-004, SH-005 |
| **~10** | Test or expectation defect / WIP | Phase 8 + Phase 7 dashboard tests + init_db / phase3_migration fixtures require SRA-804+ migrations that the user's working tree has not landed yet |
| **~11** | Documented limitation | Phase 4 dashboard PATH/async-DOM (SRA-405), Phase 4 e2e (`test_quiz_web_loop_through_real_router` — SRA-404), Phase 5 wakeup caplog quirk (SRA-509) |

The default suite reaches all `user` and `admin` mounted routes through the
client wrappers in `tests/e2e/` and the explicit integration tests. No
additional Postman scenarios are needed: the existing runner already hits
them in isolation under the policy-audit and rate-limit-inheritance tests.

## Open gaps

- Phase 8 real-OpenAI planner, conversation, and review-plan routes — SRA-802
  through SRA-809 are not yet implemented in the committed chain.
- Physical microphone, macOS menu-bar hotkey, Google OAuth, and Keychain
  acceptance — manual checks, all documented in
  [`tests/README.md`](../../tests/README.md).
- Live OpenAI extraction / chat (`make test-live`) — opted in, charges
  apply, requires `OPENAI_API_KEY`.

