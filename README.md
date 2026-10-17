# Spaced Repetition Agent

An open-source, self-hostable study agent: capture what you studied, get it resurfaced on a spaced-repetition schedule, and quiz yourself on it — built with LangChain, LangGraph, and FastAPI.

## Try the public demo

A hosted demo is available at `https://demo.spaced-repetition-agent.example`. The demo uses tighter rate limits, a 7-day retention window, and an hourly cleanup job — explore with disposable inputs, not real study data. Google sign-in is required.

## Self-hosting

The fastest path to a running instance is Docker Compose. This is part of **Phase 6 [x] — Open-source packaging** (Docker, docker-compose, demo mode, self-host docs). See the [Self-hosting with Docker](#self-hosting-with-docker) section below. macOS desktop capture (microphone, menu-bar hotkey) is supported; the desktop client is not containerized.

This repo is a **teaching project**. Phases 1–5 are implemented; the final phase ships the open-source packaging, Docker, and self-host documentation. The automated integration suite is green; physical macOS/microphone/hotkey acceptance and live Google OAuth acceptance remain the final manual checks. Supporting plumbing includes typed errors, protocols, configuration, ORM models, and database sessions. See the [Phase 2 test guide](tests/README.md) for the verification boundaries and commands.

See [`docs/plans/`](docs/plans/) for the full implementation plan and [`docs/design/technical-design.md`](docs/design/technical-design.md) for the HLD/LLD/database/API design.

## Build phases

The project is being built incrementally, smallest working slice first:

- [x] **Phase 1 — Core loop.** Single local user, no auth yet. An ingestion agent turns a pasted transcript into structured study entries, a fixed-interval scheduler (1d/3d/7d/1mo/3mo/6mo) schedules reviews, and a minimal API exposes ingest / digest / search / chat.
- [x] **Phase 2 — Voice capture.** Local Whisper transcription + a macOS menu-bar hotkey client, wired to Phase 1's ingest endpoint.
- [x] **Phase 3 — Multi-tenancy.** The OAuth-ready schema, Google browser login,
  signed web/bearer credentials, authenticated isolation, and user/admin RBAC
  are implemented, along with desktop credentials and per-user/IP rate limiting.
- [x] **Phase 4 — Daily quiz.** MCQ generation agent, nightly pre-generation job, web quiz flow.
- [x] **Phase 5 — Wake-up & voice quiz.** Per-user scheduled time, spoken digest read-out, voice quiz conversation agent.
- [x] **Phase 6 — Open-source packaging.** Docker, docker-compose, demo mode, self-host docs.
- [x] **Phase 7 — Study dashboard.** Aggregated entries, search, and upcoming-reviews timeline on `/dashboard`.
- [x] **Phase 8 — Interactive AI interview mode.** LLM-graded, source-grounded open-ended interview alongside the MCQ quiz.
- [ ] **Phase 9 — Topic authoring with references + question-quality uplift.** A `reference_materials` table + `POST /api/v1/topics` + a six-section rubric that materially improves MCQ and interview quality. See [the Phase 9 plan](docs/plans/2026-10-16-phase9-topic-authoring-and-quality.md) and the [tickets](tickets/README.md#board--phase-9-topic-authoring-with-reference-materials--question-quality-uplift).

## Phase 1 — what's here

```
src/sra/
  config.py              Settings + get_settings() [1 stub]
  db/
    models.py             User, StudyEntry, ReviewSchedule (concrete)
    session.py             DB engine/session setup (concrete)
    scheduler.py           compute_review_dates, schedule_reviews_for_entry [stubs]
    repository.py          create_entry, get_due_reviews, search_entries, mark_review_done [stubs]
  auth/
    google_oauth.py          Google identity, signed credentials, and one-use codes
    dependencies.py         require_user / require_admin security boundary
  agents/
    schemas.py               StudyEntryDraft (concrete)
    ingestion_graph.py        LangGraph: transcript -> structured entries [stubs]
    query_graph.py            LangGraph: tool-calling Q&A over your history [stubs]
  api/
    schemas.py                 Request/response DTOs (concrete)
    deps.py                     Shared dependencies re-exported for routers (concrete)
    app.py                      create_app() [stub]
    routers/
      entries.py                 POST /entries/ingest [stub]
      digest.py                  GET /digest [stub]
      search.py                  GET /search [stub]
      chat.py                    POST /chat [stub]
  alembic/                     Schema migrations (env.py wired to Settings.database_url)
tests/
  unit/          one test module per stubbed module above
  e2e/           ingest -> digest, end to end
```

Every router in `api/routers/` is mounted under `/api/v1` by `create_app()` (via `api/routers/__init__.py`'s aggregate `api_router`), so the routes above are reachable at `/api/v1/entries/ingest`, `/api/v1/digest`, etc.

Phase 1 intentionally skips voice capture — you paste/type a transcript into `/api/v1/entries/ingest` to exercise the ingestion agent and scheduler before any audio pipeline exists.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install     # runs ruff + pyright on every commit — see CONTRIBUTING.md
cp -i .env.example .env   # first setup only; fill in OPENAI_API_KEY
alembic upgrade head   # create the DB schema (see CONTRIBUTING.md for the migration workflow)

pytest -m 'not phase2' -q  # existing Phase 1 regression tests
pytest -q                 # includes intentionally red Phase 2 teaching contracts
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow (lint/type-check/tests, reproducible installs, migrations) and the DB model conventions every new table should follow.

### API rate limits

Each app owns two synchronized, in-memory token-bucket stores. Protected traffic
shares one bucket per server-resolved user ID: `/api/v1/entries/ingest`, `/search`,
`/digest`, `/chat`, `/auth/me`, `/admin/usage`, and `/admin/rate-limits` (all paths
after the first also use `/api/v1`). Authentication runs first; admin routes also
check the role before consuming capacity. The limiter runs before model calls or
route mutations, and one request consumes one token regardless of its token/model
cost. Missing/invalid credentials receive `401` without creating a bucket.

Anonymous auth traffic shares a separate bucket per client IP across
`/api/v1/auth/google/login`, `/google/callback`, `/desktop/exchange`, and `/logout`
(the last three also use `/api/v1/auth`). These routes use the IP policy even if
a cookie or bearer header is present. A normal login needs capacity for multiple
steps. `/api/health` and FastAPI's `/docs`, `/redoc`, and `/openapi.json` are exempt;
there are no other static routes currently.

| Setting | Default | Meaning |
|---|---|---|
| `RATE_LIMIT_AUTHENTICATED_CAPACITY` | `60` | Maximum immediate requests per user |
| `RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND` | `1.0` | Sustained refill of 60 requests/minute |
| `RATE_LIMIT_ANONYMOUS_CAPACITY` | `20` | Maximum immediate auth requests per IP |
| `RATE_LIMIT_ANONYMOUS_REFILL_PER_SECOND` | `0.2` | Sustained refill of 12 requests/minute |
| `RATE_LIMIT_IDLE_TTL_SECONDS` | `3600` | Remove inactive keys on the next request or summary |

Rates are continuous refill rates, not fixed windows. For a two-request burst
and two requests/minute sustained, use capacity `2` and refill `0.0333333333`.
All settings must be positive and finite; idle TTL must cover each policy's
`capacity / refill` time, so removing an idle bucket cannot restore capacity early.
Full buckets never exceed capacity. Denied requests return
`429 {"detail":"Rate limit exceeded"}` with `Retry-After` rounded up to whole
seconds; concurrent requests can consume the next token before your retry. The
desktop does not automatically resubmit ingestion after this or other failures.

The limiter reads the ASGI client address and ignores forwarding headers itself.
For direct exposure, launch Uvicorn with `--no-proxy-headers`. Behind a reverse
proxy, use `--proxy-headers --forwarded-allow-ips=<trusted-proxy-IP>` and have the
proxy replace incoming forwarding headers. Uvicorn defaults to trusting loopback
proxies; never use a wildcard trust list on an exposed listener. Without trusted
forwarding, all clients behind one proxy share its anonymous bucket. No request
parameter, `X-Forwarded-For`, or desktop-supplied ID can select a user bucket.

Admins can inspect `/api/v1/admin/rate-limits`: it returns capacities, refill
rates, and active-bucket counts, with no raw IPs, identities, credentials, or study
content. This route uses the admin's normal user bucket. Wait `Retry-After` if it
is exhausted. Restart after configuration changes. Each worker/instance has
independent buckets, and restart clears state; **run one worker** for this policy.
This is not a distributed or infrastructure-level denial-of-service defense.
A future Redis-backed atomic store can replace `TokenBucketLimiter` behind the
same consume/summary dependency boundary; Redis is not implemented.

### Upgrade an existing database for OAuth

Before upgrading a Phase 2 database, stop the application and copy the database
file to a safe backup. Point `DATABASE_URL` at the database (or a disposable
copy first), then run:

```bash
alembic upgrade head
```

The migration keeps every user ID, study-entry owner, review schedule, and
timestamp. Existing users receive the ordinary `user` role and retain a NULL
`google_sub`; NULL is the intentional intermediate state for pre-OAuth data,
not the final invariant for OAuth-created users. A visitor cannot claim this
row: SRA-302's verified login path keys users only by Google's stable subject
and will not automatically attach an unlinked local row.

To retain legacy data under a known identity, first obtain and independently
verify that account's Google subject, back up the database, and explicitly link
the numeric legacy user selected by the operator:

```bash
python -m sra.db.legacy_identity --user-id 1 --google-sub 'verified-google-subject'
```

The command refuses blank subjects, missing users, already-linked users, and a
subject already used by another row. It changes only `users.google_sub`; the
numeric user ID and all ownership remain unchanged. Run it before that Google
account's first application login. If the identity cannot be verified, leave
the row unlinked and export or archive the backed-up database instead of
guessing. Recovery is to stop the app and restore the backup. Downgrading with
`alembic downgrade 067a64508521` preserves users, entries, and reviews but
irreversibly removes Google subjects, roles, and last-login timestamps.

The implemented Phase 1 API can already run:

```bash
uvicorn sra.api.app:create_app --factory --reload --no-access-log
```

Then sign in before calling `POST /api/v1/entries/ingest`, `GET /api/v1/digest`,
`GET /api/v1/search`, or `POST /api/v1/chat`; anonymous requests now return
`401`.

### Configure Google sign-in

Create a Google OAuth web client and register the exact value of
`GOOGLE_CALLBACK_URL` (default `http://127.0.0.1:8000/api/v1/auth/google/callback`) as an authorized redirect URI. Copy `.env.example`, set
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and a high-entropy
`AUTH_SIGNING_SECRET`, then start the server and visit:

```text
http://127.0.0.1:8000/api/v1/auth/google/login
```

After Google redirects back, the server verifies the ID token, upserts by the
stable Google subject, and sets an HTTP-only SameSite session cookie. Use
`GET /api/v1/auth/me` to inspect the safe account shape and
`POST /api/v1/auth/logout` to clear the browser cookie. Provider tokens and
authorization codes are neither returned nor stored in that cookie.
The initiating browser also receives a short-lived HttpOnly login cookie;
callbacks from another browser are rejected before provider exchange. Start a
fresh login after a failed attempt. Disable access logging as shown above and
configure reverse proxies/tracing to omit auth query strings, cookies, and
Authorization headers: OAuth callback URLs contain short-lived secrets.

### Self-hosting with Docker

A multi-stage `Dockerfile` and a `docker-compose.yml` ship with the repo so a
clean clone produces a running container with one command:

```bash
git clone https://github.com/your-fork/spaced-repetition-agent.git
cd spaced-repetition-agent
cp -i .env.example .env             # fill in OPENAI_API_KEY, GOOGLE_*_SECRET, AUTH_SIGNING_SECRET
docker compose up                # dashboard at http://localhost:8000
```

The default service runs against an on-container SQLite file. To use Postgres
instead, start the optional Postgres profile:

```bash
docker compose --profile postgres up
```

### Demo mode (hosted demo)

The same image runs the hosted demo under a separate profile. The demo profile
sets `DEMO_MODE=true`, which tightens the per-user and per-IP rate limits
(capacity and refill divided by 10) and registers an APScheduler interval job
that wipes demo users whose oldest study data is older than
`DEMO_DATA_RETENTION_DAYS` (default 7 days).

```bash
docker compose --profile demo up
```

An admin can force a reset out-of-band:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
     http://localhost:8000/api/v1/admin/demo/reset
# {"deleted": 3}
```

Outside `DEMO_MODE=true` the route is still mounted but returns `503` so a
self-hoster cannot accidentally invoke the destructive path.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | Used by the ingestion and query agents |
| `DATABASE_URL` | `sqlite:///./sra.db` | SQLAlchemy URL; supports SQLite and PostgreSQL |
| `DEMO_MODE` | `false` | Enable the hosted-demo profile (tighter rate limits, hourly reset) |
| `DEMO_DATA_RETENTION_DAYS` | `7` | Days of demo data to retain before the reset job wipes it |
| `DEMO_RESET_INTERVAL_S` | `3600` | How often the demo reset job runs (seconds) |
| `RATE_LIMIT_AUTHENTICATED_CAPACITY` | `60` | Per-user token-bucket capacity |
| `RATE_LIMIT_ANONYMOUS_CAPACITY` | `20` | Per-IP token-bucket capacity for auth endpoints |
| `DEV_MODE` | `false` | Enable the synchronous quiz-pregeneration dev endpoint (see [Dev affordance](#dev-affordance)) |
| `DEV_MODE` | `false` | Enable the synchronous quiz-pregeneration dev endpoint (see [Dev affordance](#dev-affordance)) |

The full list lives in [`.env.example`](.env.example).

Local HTTP development may use `AUTH_COOKIE_SECURE=false` only with a localhost
callback. Deployed callbacks must use HTTPS and `AUTH_COOKIE_SECURE=true`.
OAuth state, desktop completion codes, and application credentials expire;
completion codes are one-use. Application credentials are stateless, so logout
clears the browser copy but cannot revoke an already stolen credential before
its configured expiry. Rotating `AUTH_SIGNING_SECRET` invalidates every current
session and bearer credential; restart all server processes after rotation.
OAuth state and desktop completion replay records are in process memory, so the
current self-hosted deployment must use one API process (or sticky routing).

To promote an already verified account, stop application traffic, back up the
database, and update only that known user's role through an operator-controlled
SQL session. Never accept a role or numeric user ID from an API request:

```sql
UPDATE users SET role = 'admin' WHERE google_sub = 'verified-google-subject';
```

Ordinary users receive `403` from `GET /api/v1/admin/usage`; admins receive only
aggregate counts. For callback failures, confirm the redirect URI matches
exactly, the server clock is correct, and all three secrets are present. Restore
configuration and start a new login attempt—failed or consumed state cannot be
reused. The default automated suite substitutes Google; a real-Google check is
manual and should use disposable test accounts and a disposable database.

## Submit a typed note from Python

Install the HTTP client extra and run the server from the previous section:

```bash
pip install -e '.[dev,capture]'
```

The client reads `API_BASE_URL` from the environment or `.env`, defaulting to
`http://127.0.0.1:8000`. Use the server origin or deployment prefix without
`/api/v1`; a trailing slash is accepted. The client needs no OpenAI, LangSmith,
database, or OAuth secrets. Desktop sign-in stores only the server-issued
application credential in Keychain and supplies it to capture requests.

```python
import httpx
from sra.client.api_client import ingest_transcript
from sra.config import get_client_settings

entries = ingest_transcript("Today I studied mitosis and the phases of cell division.")
base = get_client_settings().api_base_url.rstrip("/")
with httpx.Client(timeout=30) as client:
    for entry in entries:
        response = client.get(f"{base}/api/v1/search", params={"q": entry["topic"]})
        response.raise_for_status()
        assert entry["id"] in {row["id"] for row in response.json()}
        print("Saved and found:", entry["id"], entry["topic"])
```

Each call sends one POST containing only the transcript, with no automatic retry
or redirect following. The HTTP timeout is 90 seconds per operation, allowing for
the server's two 30-second model attempts and database work. HTTP resources close
on success and failure. Errors raise `ApiClientError`, with `status_code` and
FastAPI's `detail` when available. After a timeout or interrupted response, the
server may already have saved the note: check search before explicitly submitting
again. Settings are cached; restart the client or call
`get_client_settings.cache_clear()` after changing its environment.

See [SRA-201](tickets/SRA-201-text-capture-api-client.md) for the live verification
record and unused-port recovery check.

## Process a saved recording

With the capture extra installed and the API running, one call transcribes a
caller-owned audio file locally and submits its nonblank transcript to the
server:

```python
from pathlib import Path

from sra.capture.pipeline import process_recording

audio = Path("tests/fixtures/study_note.wav")
entries = process_recording(audio)
for entry in entries:
    print("Saved:", entry["id"], entry["topic"])
```

`WHISPER_MODEL_SIZE` selects the local model (`base` by default). Silence
returns an empty list without making an HTTP request. Transcription failures
raise `TranscriptionError`, submission failures raise `ApiClientError`, and the
pipeline never modifies or deletes the supplied audio file.

## Capture from the microphone (macOS)

With the backend running, install the desktop extra and launch the terminal recorder:

```bash
source .venv/bin/activate
pip install -e '.[dev,desktop]' -c requirements-lock.txt
python3 -m sounddevice   # inspect available devices and the default input
python3 -m sra.capture.recording
```

Choose your microphone in System Settings → Sound → Input. Allow microphone
access for the app launching Python (for example Terminal, iTerm, or your IDE)
in System Settings → Privacy & Security → Microphone, then relaunch that app if
needed. See [Apple’s microphone permissions guide](https://support.apple.com/guide/mac-help/control-access-to-the-microphone-on-mac-mchla1b1e1fe/mac).
The recorder uses the default input with mono, 16 kHz, signed 16-bit PCM through
[sounddevice’s raw stream API](https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html).

Press Enter to start, speak a note, then press Enter to stop. The command prints
`Processing recording: /…/sra-recording-….wav`, followed by the actual saved IDs
and topics, or a no-speech message. Audio buffers stay in memory while recording;
the finalized WAV exists throughout processing and is deleted on exit. Each run
owns a unique file and closes its microphone before transcription starts.

For example, say “Today I studied mitosis, the phases of cell division,” then
verify that search contains the exact IDs just reported:

```bash
curl -fsS 'http://127.0.0.1:8000/api/v1/search?q=mitosis' | python3 -m json.tool
# Substitute the path printed by the recorder after the command finishes:
test ! -e '/path/printed/by/recorder.wav' && echo 'Audio cleaned up'
```

Use your configured `API_BASE_URL` for search if different. Repeat with a
two-topic note and match both new entries. During slow transcription, you can
inspect the printed WAV path from another terminal; after completion it must
be absent. For cancellation checks, set `TMPDIR` to a dedicated existing
directory and confirm no `sra-recording-*.wav` remains there afterward.

Ctrl-C or end-of-input cancels capture with exit code 130 and no submission.
Device, transcription, and server errors exit with code 1 and clean up owned
audio. Success or silence exits with code 0. A timeout can happen after the
server saved the note: follow the printed uncertain-outcome message and search
before retrying. Try a cancelled recording, denied microphone permission, and
an unavailable backend; restore access and confirm a new run succeeds. Record
these hardware checks in [SRA-204](tickets/SRA-204-microphone-capture.md).

## macOS menu-bar capture

With the backend running, launch from a terminal in this repository:

```bash
source .venv/bin/activate
pip install -e '.[dev,desktop]' -c requirements-lock.txt
python3 -m sra.capture.menubar_app
```

Press **Control+Option+R** while studying in another app to begin recording;
press again to stop and submit. The menu also offers **Start recording**,
**Stop and save**, and **Quit**. The menu-bar title shows `SRA ●` during
recording and `SRA …` during processing. Open the menu for the full status:
saved count and IDs, no speech, or an error. Completion returns the controller
to idle while retaining the result for inspection. Saved IDs also print in the
launch terminal; compare these exact IDs with `/api/v1/search?q=YOUR_TOPIC`.

In **System Settings → Privacy & Security**, allow **Microphone** for the
application launching Python (for example Terminal or your IDE). The global
shortcut uses macOS's exact hotkey registration API and does not require
**Accessibility** or **Input Monitoring** access. If an earlier version was
granted either permission, you can remove that grant in System Settings. A
shortcut conflict leaves the recording menu available and shows a recovery
message. Importing the module does not register a shortcut, request microphone
access, or launch the UI.

Set these in `.env` or the launch shell, then restart the client:

```bash
export API_BASE_URL='http://127.0.0.1:8000'
export HOTKEY_COMBO='<ctrl>+<alt>+r'
export WHISPER_MODEL_SIZE='base'
python3 -m sra.capture.menubar_app
```

The protected Phase 3 server requires desktop sign-in. Choose **Sign in** from
the menu-bar app, complete Google consent in the system browser, and return to
the app after the browser reports success. The temporary callback listens only
on `127.0.0.1` and closes after one callback or after
`DESKTOP_LOGIN_TIMEOUT_SECONDS` (120 seconds by default). The short-lived code
is exchanged directly with `API_BASE_URL`; the application credential is never
placed in the callback URL.
Each desktop attempt supplies its own random binding, carried in the signed
server state and checked locally before exchange. **Cancel sign-in** and **Quit**
interrupt the local wait, including an incomplete callback connection. The
listener enforces an absolute deadline; exchange/account HTTP operations use
15-second network timeouts and check cancellation before storing a credential.
An already active native browser/Keychain prompt still belongs to macOS.

The resulting application credential is stored in macOS Keychain under a
service derived from `KEYCHAIN_SERVICE_PREFIX` and the normalized server origin,
with the Google subject as its account. This prevents a localhost credential
from being sent to another deployment. Keychain may show a normal access prompt
on first use. **Sign out** removes only the current server/account credential.
You can also remove it in Keychain Access by searching for the configured
service prefix. Never copy the credential into `.env` or command-line options.
After restarting the app, choose **Use saved sign-in** to validate and reuse the
last credential for this server without opening Google again.
The adapter selects the native macOS Keychain backend explicitly, independent
of global keyring configuration. Removing an inactive account preserves another
account's active selection. Restart tests recreate the adapter against a fake
secure backend; real Keychain persistence still requires manual acceptance.

An expired or rejected credential produces a “sign in again” error and is
removed from the selected storage entry when loaded and validated (the active
selector and named account are separate Keychain entries). A failed authenticated ingestion is
not replayed automatically: even after signing in again, check search before
making an explicit new submission because the original POST outcome may be
uncertain.

Choose a shortcut that does not conflict with another app. Shortcuts require at
least one of `ctrl`, `alt`/`option`, `shift`, or `cmd`/`command`, plus a letter,
number, F1–F12, Space, Tab, Enter, or Escape. Malformed or unsupported shortcuts
produce a startup error. For microphone errors, check permission and the default
input device in Sound settings, reconnect the device, and start a fresh capture.
For server errors, check the backend and `API_BASE_URL` (without `/api/v1`);
restore connectivity before recording again. Transcription errors require checking
the model installation/cache; the first capture may download model weights.
Timeouts report an unknown save outcome: search before trying again, since the
server may already have saved the note. The app never automatically resubmits.

Processing runs on a background worker; additional toggles are ignored until it
finishes. Quit discards an unfinished recording. If processing has already begun,
Quit waits for that submission and temporary-audio cleanup before exiting; it
cannot undo a server save. First-run model loading can extend this wait.
Only transcript text crosses the HTTP boundary; extraction and scheduling remain
on the backend. macOS extras are unnecessary for running the backend.

## Phase 2 — verification

Run `make test-data` for the full local fixture suite (including real cached
Whisper and a temporary HTTP server/database), or `make test-live` for the real
OpenAI extraction/chat flow using the synthetic audio fixture. Install
`.[dev,capture]` and cache the Whisper `base` model first. The live command uses
your configured OpenAI key and incurs API charges. See the
[test guide](tests/README.md#two-complete-verification-commands) for setup,
coverage, and desktop simulation limits.

SRA-201 through SRA-205 are implemented. Continue with SRA-206's real desktop
acceptance. Automated tests include flows through the API/database/scheduler;
the [test guide](tests/README.md) distinguishes unit, end-to-end, real Whisper,
and manual desktop checks.

Default tests only need `'.[dev]'`. Install `'.[dev,capture]'` for the HTTP client
and local `faster-whisper` transcription. The model is constructed only when
transcription is requested; first use may download model weights, which later
runs reuse from the local cache. The `desktop` extra adds macOS microphone and
menu-bar dependencies. Client settings are `API_BASE_URL` (default
`http://127.0.0.1:8000`, without `/api/v1`), `WHISPER_MODEL_SIZE` (`base`), and
`HOTKEY_COMBO` (`<ctrl>+<alt>+r`), `DESKTOP_LOGIN_TIMEOUT_SECONDS` (`120`), and
`KEYCHAIN_SERVICE_PREFIX` (`com.spaced-repetition-agent.desktop`). The client
needs no Google client secret or application signing secret.
The menu-bar launch and exact hotkey registration are covered by SRA-205. Real
microphone and desktop acceptance remain manual checks; see each ticket’s
verification record.

Both agents use OpenAI through `langchain-openai`. Set `OPENAI_API_KEY` in
your existing `.env` or shell environment. `OPENAI_MODEL` defaults to
`gpt-4.1-nano`, an inexpensive model supporting structured output and tool
calling. Change that setting and restart the server to select another compatible
OpenAI model. Anthropic credentials are no longer used.

Each model response is capped at 2,048 completion tokens, with a 30-second
request timeout and at most one retry. Ingestion uses function calling with
Pydantic validation; chat uses database-backed tools. API calls are billed to
your OpenAI account. See the [official model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-nano).

The digest includes pending reviews due today or earlier. A newly captured
entry's first checkpoint is scheduled for tomorrow and appears once it is due.

## LangSmith tracing

Tracing is optional and disabled by default. To enable it, add these settings
to `.env` (or export them in your shell), then restart the API:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-langsmith-api-key
LANGSMITH_PROJECT=spaced-repetition-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Use the endpoint for your LangSmith region (for example,
`https://eu.api.smith.langchain.com` for EU). If your API key is linked to
multiple workspaces, also set `LANGSMITH_WORKSPACE_ID`.

Call `/api/v1/entries/ingest` or `/api/v1/chat`, then open your project in
[LangSmith](https://smith.langchain.com). Look for `sra-ingestion` and
`sra-query`: each trace includes graph nodes and nested LangChain model/tool
calls, including inputs, outputs, timing, and errors. Traces include transcript,
chat, and retrieved study content. Set `LANGSMITH_TRACING=false` and restart
to disable uploads.

The routes use `sra.agents.tracing.trace_agent()` to pass Pydantic's `.env`
settings to LangSmith without changing process environment variables. If you
invoke a graph from a standalone Python script, wrap its invocation too:

```python
from sra.agents.tracing import trace_agent

with trace_agent():
    result = graph.invoke(initial_state)  # your compiled graph and input
```

See the [LangGraph tracing guide](https://docs.langchain.com/langsmith/trace-with-langgraph)
for details. No extra decorators are needed on the existing LangChain tools or models.

## Dev affordance

With `DEV_MODE=true` in `.env` (or the launch shell), a logged-in user can
trigger today's quiz pregeneration synchronously without waiting for the
4 AM UTC cron:

```bash
curl -X POST -b 'sra_session=…' \
  'http://127.0.0.1:8000/api/v1/me/dev/pregenerate-now?include_upcoming=true'
# {"summary": {"users": 0, "entries_seen": 3, "sets_created": 3,
#  "sets_skipped": 0, "errors": 0}, "today": "2026-10-15",
#  "include_upcoming": true,
#  "quiz_sets": [{"quiz_set_id": 7, "entry_id": 12,
#   "topic": "Mitosis", "generated_on": "2026-10-15",
#   "question_count": 10, "option_count": 4}, …]}
```

`include_upcoming` defaults to `true` in dev so freshly-ingested entries
(whose first review is scheduled for tomorrow) immediately get a quiz set
dated today. Set it to `false` to keep the cron-equivalent behavior:
generate a quiz set only for entries that already have a review due today.
The endpoint returns `503` when `DEV_MODE=false` and never rewrites an
already-scheduled `ReviewSchedule` row. Use it to drive the digest → quiz
→ MCQ → attempt pipeline end to end during development, then read the
generated MCQs through `GET /api/v1/quiz/{quiz_set_id}` or start an
attempt through
`POST /api/v1/quiz/{quiz_set_id}/attempts {"mode": "web"}`.

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community standards. Work is tracked as tickets in [`tickets/`](tickets/README.md).

## License

[MIT](LICENSE)
