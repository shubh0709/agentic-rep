# SRA-201 — Text capture through the HTTP client

**Type:** Task
**Phase:** 2 — Voice capture
**Priority:** High — first working desktop-to-server slice
**Status:** Client configuration implemented — HTTP client and manual verification pending
**Blocked by:** SRA-107 (done)
**Blocks:** SRA-202

## TDD starting point

The assistant has supplied this ticket's tests, typed stubs, and teaching docstrings. **You write the learning-function implementations.** See the [test-first guide](../tests/README.md) for the unit/e2e boundary and implementation order. Focused tests begin red at `NotImplementedError`; after implementing this ticket they must pass. Keep Phase 1 regressions green with `pytest -m 'not phase2' -q`. Later exercises remain visibly red until implemented; the complete default suite must be green for SRA-206. The expected passing results below describe the implemented ticket, not the current scaffold.

## Summary

Build the client configuration and ingestion HTTP wrapper together. The finished ticket lets a developer submit a study note from Python, receive created entries from the existing Phase 1 server, and retrieve those exact entries through search.

## Where things stand right now

Phase 1 already ingests text, stores entries, and schedules reviews. The `client/` package now contains teaching stubs; HTTP behavior remains to be implemented. The current server `Settings` requires `OPENAI_API_KEY`; the desktop side needs its own configuration so it can talk to the server without holding that key.

Client configuration is now implemented: `ClientSettings` validates the server URL and `get_client_settings()` caches it independently of server settings. All 24 configuration tests pass. The HTTP wrapper and complete manual flow remain pending.

## What this ticket unlocks

**Typed study note → Python client → running API → stored entries → search by returned ID.** Configuration and packaging are part of delivering this working flow. This ticket cannot close with only settings or stub tests working.

## Files

- New: `src/sra/client/__init__.py`, `src/sra/client/api_client.py`
- `src/sra/config.py`, `.env.example`, `pyproject.toml`, `requirements-lock.txt`
- New: `tests/unit/test_client_config.py`, `tests/unit/test_api_client.py`, `tests/e2e/test_client_ingestion_e2e.py`
- `README.md`, `CONTRIBUTING.md`

## What to implement

- Implement the supplied learning-target stubs against their tests; the teaching docstrings explain the contracts.
- Add `ClientSettings` and cached `get_client_settings()` in `config.py`. Read `API_BASE_URL` (default `http://127.0.0.1:8000`, without `/api/v1`) from environment variables / `.env`; validate a usable HTTP(S) server URL. Load without OpenAI, LangSmith, database, or OAuth credentials. Keep the server's required-key contract and cache independent.
- Add an optional `capture` dependency group containing the HTTP runtime library. Later audio tickets extend this group. Keep the base server installation independent of desktop dependencies, update the lock snapshot, and document installation and the server URL.
- `ingest_transcript(transcript: str) -> list[dict[str, Any]]` — POST `{"transcript": transcript}` to `/api/v1/entries/ingest`, normalizing a trailing slash on the base URL, and return the entry objects from a valid `201` response. Use a finite, documented timeout that allows for the server's model-call budget and close HTTP resources reliably.
- `ApiClientError` — expose connection failures, timeouts, non-201 responses, invalid JSON, and malformed entry-list responses with useful details. Preserve the HTTP status and FastAPI's `detail` when present. A timeout after submission can mean the server already saved the note; explain that uncertainty.
- Attempt one POST per call, with no automatic retry. The server resolves the local user; send no caller-supplied user ID, audio, OAuth token, or LLM key. Do not import agents, repositories, or server settings into the client.
- Add an e2e test through the actual client wrapper and FastAPI route using in-process HTTP transport, an isolated DB, and fake model inference. Confirm returned IDs, persisted transcript/ownership, six pending reviews per entry, and search visibility. Do not replace the ingestion wrapper or route with a fake in this test.

## Where to look

- `src/sra/api/routers/entries.py` and `src/sra/api/schemas.py` for the actual Phase 1 contract.
- `src/sra/config.py`, `tests/unit/test_config.py`, and `tests/conftest.py` for configuration and test wiring.
- Technical design §1.2 and §3.29 for the thin-client boundary. Use current OpenAI-backed Phase 1 behavior; auth remains Phase 3.

## Acceptance criteria

- [x] Client configuration works without server credentials and honors environment overrides; server configuration still requires its OpenAI key
- [ ] The client sends the correct JSON/path for base URLs with and without a trailing slash, returning entries from a valid `201`
- [ ] Connection, timeout, HTTP, and response-shape failures raise useful `ApiClientError`s without retrying or claiming an uncertain save definitely failed
- [ ] The e2e test verifies real client/API/storage/scheduling/search wiring with only transport location and model inference substituted
- [ ] The manual flow below creates entries against a running server and finds their exact returned IDs in search
- [ ] Pointing at an unused port produces a clear error; restoring the URL permits a successful new submission

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_client_config.py tests/unit/test_api_client.py -v
pytest tests/e2e/test_client_ingestion_e2e.py -v
```

Expected: all tests pass without a live server or model. These test files are already supplied. Finish with this ticket's tests, `pytest -m 'not phase2' -q`, `ruff check .`, and `pyright`; inspect the full suite for failures outside later unimplemented exercises.

## Manual verification

Install the client extra and start the Phase 1 server with its configured OpenAI key:

```bash
pip install -e ".[dev,capture]"
uvicorn sra.api.app:create_app --factory --reload --port 8000
```

In a second terminal, run the actual client and verify each returned ID through search:

```bash
API_BASE_URL=http://127.0.0.1:8000 python3 - <<'PY'
import httpx
from sra.client.api_client import ingest_transcript
from sra.config import get_client_settings

entries = ingest_transcript("Today I studied mitosis and the phases of cell division.")
assert entries, "No entries created"
base = str(get_client_settings().api_base_url).rstrip("/")
with httpx.Client(timeout=30) as client:
    for entry in entries:
        response = client.get(f"{base}/api/v1/search", params={"q": entry["topic"]})
        response.raise_for_status()
        assert entry["id"] in {row["id"] for row in response.json()}
        print("Saved and found:", entry["id"], entry["topic"], entry["summary"])
PY
```

Each entry must print `Saved and found` with its new ID. An older note with the same topic is not sufficient evidence. This uses the server's real model; the client itself needs no model key. Repeat with an unused port, confirm an error instead of success, then restore the URL and confirm recovery. Do not resubmit automatically after an ambiguous timeout; check search first.

## Verification record

- Date, server URL, and commands used:
- Created entry IDs and matching search IDs:
- Invalid-port result and recovery result:
- Focused/full test, lint, and type-check results:

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Real client → API → stored entry → search flow verified and recorded above
- [ ] Failure and recovery observed; no subsequent Phase 2 ticket is needed to run this check
- [ ] Focused/completed-ticket tests, Phase 1 regressions, `ruff check .`, and `pyright` pass (all default tests must pass by SRA-206); setup documentation updated
