# SRA-201 — Text capture through the HTTP client

**Phase:** 2 — Voice capture · **Status:** verified

## Summary

Build the client configuration and ingestion HTTP wrapper together, so a
developer can submit a study note from Python, receive created entries from the
existing Phase 1 server, and retrieve those exact entries through search. The
`capture` extra installs HTTPX; SRA-202 adds Whisper. Client configuration is
independent of server `Settings`, so the client needs no server credentials.

## Unlocks

- A reusable submission path that both a typed-note script and the later voice
  pipeline call. SRA-202 supplies text from a recording; SRA-203 composes the two;
  SRA-204/205 let microphone and hotkey callers start that flow.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/client/api_client.py`](../src/sra/client/api_client.py) | edit | `ingest_transcript(transcript: str) -> list[dict[str, Any]]` — one POST to `/api/v1/entries/ingest`, normalizing a trailing slash on the base URL, returning entry objects from a valid `201`. `ApiClientError` carries connection failures, timeouts, non-201 responses, invalid JSON, and malformed entry lists. |
| [`src/sra/config/`](../src/sra/config/) | edit | `ClientSettings` + cached `get_client_settings()` read `API_BASE_URL` (default `http://127.0.0.1:8000`, no `/api/v1`) and validate an HTTP(S) URL — with no OpenAI, LangSmith, database, or OAuth credential loaded. |
| [`tests/unit/test_api_client.py`](../tests/unit/test_api_client.py) | run | 50 tests over request/response and failure behavior with controlled HTTP responses. |
| [`tests/e2e/test_client_ingestion_e2e.py`](../tests/e2e/test_client_ingestion_e2e.py) | run | 3 tests through the real route, graph, storage, scheduling, and search; also simulates a response lost after saving. |

## Acceptance criteria

- [x] Client configuration works without server credentials and honors
  environment overrides; server configuration still requires its OpenAI key
- [x] The client sends the correct JSON/path for base URLs with and without a
  trailing slash, returning entries from a valid `201`
- [x] Connection, timeout, HTTP, and response-shape failures raise useful
  `ApiClientError`s without retrying or claiming an uncertain save definitely
  failed
- [x] The e2e test verifies real client/API/storage/scheduling/search wiring with
  only transport location and model inference substituted
- [x] The manual flow below creates entries against a running server and finds
  their exact returned IDs in search
- [x] Pointing at an unused port produces a clear error; restoring the URL permits
  a successful new submission

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_client_config.py tests/unit/test_api_client.py -v
pytest tests/e2e/test_client_ingestion_e2e.py -v
pytest -m 'not phase2' -q
```

Passes without a live server or model. Finish with `ruff check .` and `pyright`;
the full suite is red only on later unimplemented exercises until SRA-206.

## Manual verification

- [ ] With `pip install -e ".[dev,capture]"` and the server running, this prints
      `Saved and found` per new entry — an older note with the same topic is not
      sufficient evidence:

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

- [ ] Repeat with an unused port, confirm an error instead of success, then
      restore the URL and confirm recovery.

> **Note:** changing `API_BASE_URL` within one Python process requires
> `get_client_settings.cache_clear()` before the next submission.

### Verification record

- **2026-10-13, 20:15 IST**, server `http://127.0.0.1:8765`, real `gpt-4.1-nano`
  inference, isolated temp DB at `/tmp/sra-201-verification/live.db`. The client
  ran outside the repo's `.env` with all server credential variables removed.
- First submission returned **ID 1** (mitosis); search returned **[1]**. Unused
  port **53425** gave `ApiClientError: Could not connect to
  http://127.0.0.1:53425/api/v1/entries/ingest: [Errno 61] Connection refused` —
  no success, no retry. Restoring the URL, the second submission returned **ID 2**
  (Photosynthesis); search returned **[2]**. Both retain their exact transcripts,
  belong to **local@localhost**, and have **six pending reviews** each.
- Clean install of `-r requirements-lock.txt` then `-e '.[dev,capture]' -c
  requirements-lock.txt` passed `pip check`; `pip freeze --exclude-editable`
  matches the committed snapshot exactly, including `httpx==0.28.1`.
- **77 focused tests passed** (24 config, 50 client, 3 e2e); Phase 1 **91 passed**.
  Full suite: **171 passed, 75 failed, 2 skipped** — every failure a
  `NotImplementedError` in SRA-202–205. `ruff check .`, `pyright`, and
  `git diff --check` pass.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).