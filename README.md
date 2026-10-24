# Spaced Repetition Agent

An open-source, self-hostable study agent: capture what you studied, get it resurfaced on a
spaced-repetition schedule, and quiz yourself on it — built with LangChain, LangGraph, and FastAPI.
Every other fact has one home (see [Documentation map](#documentation-map)).

## Try the public demo

A hosted demo is available at `https://demo.spaced-repetition-agent.example`. The demo uses tighter rate limits, a 7-day retention window, and an hourly cleanup job — explore with disposable inputs, not real study data. Google sign-in is required.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # add [capture] for local Whisper, [desktop] for macOS audio
cp -i .env.example .env      # first setup only; then fill in OPENAI_API_KEY
pre-commit install           # ruff + pyright on every commit
alembic upgrade head
uvicorn sra.api.app:create_app --factory --reload --no-access-log
```

`pytest -q` runs the default suite; `make test-data` adds real cached-Whisper fixtures and
`make test-live` calls the real OpenAI API (costs money). Setup, lockfile, and migrations are in
[`CONTRIBUTING.md`](CONTRIBUTING.md), per-ticket commands in [`tests/README.md`](tests/README.md).
Sign in at `/api/v1/auth/google/login` first — anonymous requests get `401`; docs are at `/docs`.

## Build phases

| Phase | Scope |
|---|---|
| Phase 1 | Core loop: ingestion agent, scheduler, minimal API |
| Phase 2 | Voice capture: local Whisper + menu-bar hotkey |
| Phase 3 | Multi-tenancy: OAuth, credentials, RBAC, limits |
| Phase 4 | Daily quiz: MCQ agent, nightly pre-generation |
| Phase 5 | Wake-up & voice quiz: schedule, spoken digest |
| Phase 6 | Packaging: Docker, demo mode, self-host docs |
| Phase 7 | Study dashboard: entries, search, timeline |
| Phase 8 | Interactive AI interview mode |
| Phase 9 | [Topic authoring + question-quality uplift](tickets/README.md#phase-9--topic-authoring-with-reference-materials--question-quality-uplift) |
| Phase 10 | Explicit model boundaries and boundary-first tests |
| Phase 11 | On-demand MCQ and AI interview assessments |
| Phase 12 | Calm Today workspace, daily inspiration, subtle motion, and optional website voice |

Phase status, scope changes, and verification results are owned by [`PROGRESS.md`](PROGRESS.md),
the source of truth. Ticket inventory and phase → ticket map:
[`tickets/README.md`](tickets/README.md).

## Submit a study entry

With the server running and a signed-in session, this is the whole loop:

```bash
# Capture: the ingestion agent extracts entries and schedules their reviews
curl -s -X POST http://localhost:8000/api/v1/entries/ingest \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Today I studied mitosis and the phases of cell division."}'

# Search finds it immediately
curl -s "http://localhost:8000/api/v1/search?q=mitosis"

# Digest lists reviews due today or earlier (a new entry's first checkpoint is tomorrow)
curl -s http://localhost:8000/api/v1/digest

# Chat asks the query agent over your own history
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "what did I study today?"}'
```

### API reference

`create_app()` mounts every router under `/api/v1`; `/api/health` is the one deliberately unversioned
route. The full route table, request/response shapes, status codes, and error envelope are the API
contract in [§4 of the technical design](docs/design/technical-design.md#4-api-contract).

### API rate limits

Two synchronized in-memory token buckets per instance: one per resolved user ID, shared by
`/api/v1/entries/ingest`, `/search`, `/digest`, `/chat`, `/auth/me`, and both `/admin/*` routes; one
per client IP across the four `/api/v1/auth` entry points. Authentication runs first, admin routes
check the role before consuming capacity, one request costs one token whatever it costs the model,
and missing credentials get `401` with no bucket created. `/api/health` and the FastAPI docs routes
are exempt, `DEMO_MODE=true` divides capacity and refill by 10, and denials return `429` with
`Retry-After` in whole seconds.

The limiter ignores forwarding headers itself: pass `--no-proxy-headers` when exposed directly, or
`--proxy-headers --forwarded-allow-ips=<trusted-proxy-IP>` behind a proxy that rewrites them — never
a wildcard list. Buckets are per worker and cleared on restart, so **run one worker**; this is not a
distributed defense, and nothing in a request can select another user's bucket. `GET
/api/v1/admin/rate-limits` reports capacities and bucket counts with no IPs or study content.
Names, defaults, and the planned Redis seam: the env table below and
[`CONTRIBUTING.md`](CONTRIBUTING.md#running-checks-manually).

### Environment variables

Server side, from `Settings` in `src/sra/config/`:

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | Ingestion, query, quiz, interview |
| `OPENAI_MODEL` | `gpt-4.1-nano` | Structured-output / tool-calling model |
| `OPENAI_VERIFY_MCQS` | `true` | Per-question verifier pass |
| `DATABASE_URL` | `sqlite:///./sra.db` | SQLite or PostgreSQL |
| `GOOGLE_CLIENT_ID` | — (empty; required to sign in) | OAuth web client ID |
| `GOOGLE_CLIENT_SECRET` | — (empty; required to sign in) | Server-only, never a client env |
| `GOOGLE_CALLBACK_URL` | `http://127.0.0.1:8000/api/v1/auth/google/callback` | Register exactly in Google Cloud |
| `GOOGLE_AUTHORIZATION_ENDPOINT` | `https://accounts.google.com/o/oauth2/v2/auth` | Provider override |
| `GOOGLE_TOKEN_ENDPOINT` | `https://oauth2.googleapis.com/token` | Provider override |
| `AUTH_SIGNING_SECRET` | — (empty; required to sign in) | ≥ 32 chars, e.g. `openssl rand -hex 32` |
| `AUTH_CREDENTIAL_TTL_SECONDS` | `3600` | Bearer credential lifetime |
| `AUTH_REFRESH_TTL_SECONDS` | `604800` | Refresh lifetime (7 days) |
| `OAUTH_STATE_TTL_SECONDS` | `600` | Google login state lifetime |
| `DESKTOP_COMPLETION_TTL_SECONDS` | `60` | One-use desktop code lifetime |
| `AUTH_COOKIE_NAME` | `sra_session` | Session cookie name |
| `AUTH_COOKIE_SECURE` | `false` | Must be `true` off localhost |
| `AUTH_COOKIE_SAMESITE` | `lax` | `lax` or `strict` |
| `AUTH_WEB_REDIRECT_URL` | `/` | Post-login redirect |
| `RATE_LIMIT_AUTHENTICATED_CAPACITY` | `60` | Per-user burst |
| `RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND` | `1.0` | Per-user sustained refill |
| `RATE_LIMIT_ANONYMOUS_CAPACITY` | `20` | Per-IP auth burst |
| `RATE_LIMIT_ANONYMOUS_REFILL_PER_SECOND` | `0.2` | Per-IP sustained refill |
| `RATE_LIMIT_IDLE_TTL_SECONDS` | `3600` | Idle-key expiry; ≥ each refill time |
| `SCHEDULER_ENABLED` | `true` | Run APScheduler jobs |
| `SCHEDULER_PREGEN_HOUR_UTC` | `4` | Quiz pre-generation cron hour |
| `SCHEDULER_WAKEUP_INTERVAL_S` | `60` | Wake-up tick interval |
| `DEMO_MODE` | `false` | Tighter limits, hourly reset |
| `DEMO_DATA_RETENTION_DAYS` | `7` | Demo data retention |
| `DEMO_RESET_INTERVAL_S` | `3600` | Demo reset interval |
| `DEV_MODE` | `false` | Enable [Dev affordance](#dev-affordance) |
| `LANGSMITH_TRACING` | `false` | Upload traces (incl. study content) |
| `LANGSMITH_API_KEY` | — (empty; required when tracing) | LangSmith key |
| `LANGSMITH_PROJECT` | `spaced-repetition-agent` | LangSmith project |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Region endpoint |
| `LANGSMITH_WORKSPACE_ID` | — (unset) | Multi-workspace keys only |

Capture client, from `ClientSettings`; it needs no server secret:

| Variable | Default | Meaning |
|---|---|---|
| `API_BASE_URL` | `http://127.0.0.1:8000` | Server origin, without `/api/v1` |
| `WHISPER_MODEL_SIZE` | `base` | Local faster-whisper model |
| `HOTKEY_COMBO` | `<ctrl>+<alt>+r` | macOS global hotkey |
| `DESKTOP_LOGIN_TIMEOUT_SECONDS` | `120` | Desktop sign-in deadline |
| `KEYCHAIN_SERVICE_PREFIX` | `com.spaced-repetition-agent.desktop` | Keychain namespace |

`AUTH_COOKIE_NAME`, `GOOGLE_AUTHORIZATION_ENDPOINT`, `GOOGLE_TOKEN_ENDPOINT`, `OPENAI_VERIFY_MCQS`,
`SCHEDULER_ENABLED`, `SCHEDULER_PREGEN_HOUR_UTC`, and `SCHEDULER_WAKEUP_INTERVAL_S` have no
[`.env.example`](.env.example) entry, so their defaults above apply as-is.

## OAuth / database upgrades

### Upgrade an existing database for OAuth

Back up the database file first — a Phase 1/2 database predates the OAuth columns. Stop the app,
point `DATABASE_URL` at it (or a disposable copy), and run `alembic upgrade head`; the workflow is in
[`CONTRIBUTING.md`](CONTRIBUTING.md#schema-migrations-alembic). User IDs, entry owners, schedules,
and timestamps survive; existing users get the `user` role and keep a NULL `google_sub`. To keep
legacy data under a known identity, verify that account's Google subject first, then:

```bash
python -m sra.db.legacy_identity --user-id 1 --google-sub 'verified-google-subject'
```

It refuses blank subjects, missing users, already-linked users, and a subject another row uses, and
changes only `users.google_sub`; recovery is restoring the backup. Promotion to admin is an operator
SQL update (`UPDATE users SET role = 'admin' WHERE google_sub = '...';`), never an API field.

### Configure Google sign-in

Create a Google OAuth **web** client in Google Cloud, register `GOOGLE_CALLBACK_URL` exactly as an
authorized redirect URI, and set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and a high-entropy
`AUTH_SIGNING_SECRET` (`openssl rand -hex 32`). Visiting `/api/v1/auth/google/login` makes the
callback verify the ID token, upsert by the stable Google subject, and set an HTTP-only SameSite
session cookie; `GET /api/v1/auth/me` shows the account and `POST /api/v1/auth/logout` clears it.

`AUTH_COOKIE_SECURE=false` is allowed only for a localhost callback — deployments need HTTPS and
`true`. OAuth callback URLs carry short-lived secrets, so keep access logging and tracing from
recording query strings, cookies, and `Authorization` headers. Rotating `AUTH_SIGNING_SECRET`
invalidates every session and bearer credential: restart after rotation. OAuth state and desktop
replay records live in process memory, so run one API process or use sticky routing. The automated
suite substitutes Google; a real-Google check is manual and should use a disposable account.

## Self-hosting

### Self-hosting with Docker

A multi-stage `Dockerfile` and `docker-compose.yml` ship with the repo, so a clean clone produces a
running container with one command:

```bash
git clone https://github.com/your-fork/spaced-repetition-agent.git
cd spaced-repetition-agent
cp -i .env.example .env   # fill in OPENAI_API_KEY, GOOGLE_CLIENT_SECRET, AUTH_SIGNING_SECRET
docker compose up         # dashboard at http://localhost:8000
```

The default service uses an on-container SQLite file; `--profile postgres` uses PostgreSQL instead.
macOS desktop capture is supported but not containerized — the backend alone serves the dashboard,
ingest, digest, quiz, and interview.

### Demo mode

`docker compose --profile demo up` sets `DEMO_MODE=true`, dividing rate limit capacity and refill by
10 and registering a job that wipes demo users whose oldest study data is older than
`DEMO_DATA_RETENTION_DAYS` (default 7) every `DEMO_RESET_INTERVAL_S` (default 3600) seconds. An
admin can force a reset out of band with
`curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" http://localhost:8000/api/v1/admin/demo/reset`,
which returns `{"deleted": 3}`. Outside `DEMO_MODE=true` the route is still mounted but returns
`503`, so a self-hoster cannot accidentally invoke the destructive path.

## Voice capture (macOS)

With the backend running and `pip install -e '.[dev,desktop]'`:

```bash
python3 -m sra.capture.recording     # terminal recorder: Enter to start, Enter to stop
python3 -m sra.capture.menubar_app   # menu-bar app: Control+Option+R toggles recording
```

Grant Microphone access (System Settings → Privacy & Security → Microphone) to whatever app launches
Python; the global hotkey needs no Accessibility or Input Monitoring grant. Only transcript text
crosses the HTTP boundary. Recording flow, menu, `Sign in` / `Use saved sign-in`, Keychain storage,
and the cancellation and cleanup exit codes are in
[SRA-204](tickets/SRA-204-microphone-capture.md) and
[SRA-205](tickets/SRA-205-menubar-hotkey-capture.md); the shared transcription → ingest pipeline is
[SRA-203](tickets/SRA-203-recording-to-ingestion-pipeline.md).

## LangSmith tracing

Disabled by default. Set `LANGSMITH_TRACING=true` plus `LANGSMITH_API_KEY` in `.env` and restart the
API; traces appear under the `sra-ingestion` and `sra-query` graphs and include study content.

## Dev affordance

With `DEV_MODE=true`, a signed-in user can trigger today's quiz pre-generation synchronously instead
of waiting for the 04:00 UTC cron:

```bash
curl -X POST -b 'sra_session=…' \
  'http://127.0.0.1:8000/api/v1/me/dev/pregenerate-now?include_upcoming=true'
# {"summary": {"users": 0, "entries_seen": 3, "sets_created": 3, "sets_skipped": 0,
#  "errors": 0}, "today": "2026-10-15", "include_upcoming": true, "quiz_sets": [...]}
```

`include_upcoming` defaults to `true` in dev so freshly-ingested entries still get a quiz set today.
The endpoint never rewrites a scheduled `ReviewSchedule` and returns `503` when `DEV_MODE=false`;
read the MCQs through `GET /api/v1/quiz/{quiz_set_id}`.

## Contributing

Contributions are welcome — [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow,
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community standards,
[`tickets/`](tickets/README.md) for the work queue.

## License

[MIT](LICENSE)

## Documentation map

Each fact has exactly one home; follow the link instead of rereading it here:

| Topic | Owning file |
|---|---|
| What it is, install, quickstart, env vars, self-hosting | `README.md` |
| Phase status and what is done | [`PROGRESS.md`](PROGRESS.md) |
| Ticket inventory and phase → ticket mapping | [`tickets/README.md`](tickets/README.md) |
| Architecture, DB schema, modules, API contract | [`technical-design.md`](docs/design/technical-design.md#4-api-contract) |
| Dev setup, lint/type/test, Alembic, models | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Test commands, scaffold failures, manual evidence | [`tests/README.md`](tests/README.md) |
| Dashboard assets and consumed endpoints | [`dashboard/README.md`](dashboard/README.md) |
| Environment template | [`.env.example`](.env.example) |
| Historical plans | [`docs/plans/`](docs/plans/) |
| Workflows: commits, tickets, TDD, self-heal | [`guides/`](guides/) |
| Agent instructions | [`AGENTS.md`](AGENTS.md) |
