# Spaced Repetition Agent

An open-source, self-hostable study agent: capture what you studied, get it resurfaced on a spaced-repetition schedule, and quiz yourself on it — built with LangChain, LangGraph, and FastAPI.

This repo is a **teaching project**. Phase 1 and all Phase 2 runtime components
are implemented: the text client, local transcription, recording pipeline,
microphone recorder, and macOS menu-bar hotkey client. The automated integration
suite is green; Phase 2 remains open until its physical microphone/hotkey and
live-server acceptance is recorded. Supporting plumbing includes typed errors,
protocols, configuration, ORM models, and database sessions. See the
[Phase 2 test guide](tests/README.md) for the verification boundaries and commands.

See [`docs/plans/`](docs/plans/) for the full implementation plan and [`docs/design/technical-design.md`](docs/design/technical-design.md) for the HLD/LLD/database/API design.

## Build phases

The project is being built incrementally, smallest working slice first:

- [x] **Phase 1 — Core loop.** Single local user, no auth yet. An ingestion agent turns a pasted transcript into structured study entries, a fixed-interval scheduler (1d/3d/7d/1mo/3mo/6mo) schedules reviews, and a minimal API exposes ingest / digest / search / chat.
- [ ] **Phase 2 — Voice capture.** Local Whisper transcription + a macOS menu-bar hotkey client, wired to Phase 1's ingest endpoint.
- [ ] **Phase 3 — Multi-tenancy.** Google OAuth login, real per-user data isolation, RBAC (user/admin), API rate limiting.
- [ ] **Phase 4 — Daily quiz.** MCQ generation agent, nightly pre-generation job, web quiz flow.
- [ ] **Phase 5 — Wake-up & voice quiz.** Per-user scheduled time, spoken digest read-out, voice quiz conversation agent.
- [ ] **Phase 6 — Open-source packaging.** Docker, docker-compose, demo mode, self-host docs.

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
    dependencies.py         get_current_user — Phase 1 placeholder (concrete; real OAuth lands in Phase 3)
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
cp .env.example .env   # first setup only; fill in OPENAI_API_KEY
alembic upgrade head   # create the DB schema (see CONTRIBUTING.md for the migration workflow)

pytest -m 'not phase2' -q  # existing Phase 1 regression tests
pytest -q                 # includes intentionally red Phase 2 teaching contracts
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow (lint/type-check/tests, reproducible installs, migrations) and the DB model conventions every new table should follow.

The implemented Phase 1 API can already run:

```bash
uvicorn sra.api.app:create_app --factory --reload
```

Then `POST /api/v1/entries/ingest {"transcript": "..."}`, `GET /api/v1/digest`, `GET /api/v1/search?q=...`, `POST /api/v1/chat {"question": "..."}`.

## Submit a typed note from Python

Install the HTTP client extra and run the server from the previous section:

```bash
pip install -e '.[dev,capture]'
```

The client reads `API_BASE_URL` from the environment or `.env`, defaulting to
`http://127.0.0.1:8000`. Use the server origin or deployment prefix without
`/api/v1`; a trailing slash is accepted. The client needs no OpenAI, LangSmith,
database, or OAuth credentials. The running server owns model calls and resolves
the local user.

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
`HOTKEY_COMBO` (`<ctrl>+<alt>+r`). The client needs no server credentials.
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

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community standards. Work is tracked as tickets in [`tickets/`](tickets/README.md).

## License

[MIT](LICENSE)
