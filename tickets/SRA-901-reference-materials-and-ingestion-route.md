# SRA-901 — Reference material schema, URL fetcher, doc extractor, ingestion route

**Type:** Implementation
**Phase:** 9 — Topic authoring with reference materials + question-quality uplift
**Priority:** High (unblocks 902-904)
**Status:** Planned
**Blocked by:** SRA-509 (Phase 5 verified) and the existing SRA-603 cascade (`delete_user_cascade`)
**Blocks:** SRA-902 (dashboard UI), SRA-903 (quiz graph uplift), SRA-904 (interview uplift)

## Summary

Adds the data model, server-side ingestion route, the URL-fetch helper, and the
document-text extractor that the rest of Phase 9 builds on. After this ticket:
an authenticated user can `POST /api/v1/topics` with a topic plus a list of
references whose `kind` is one of `url`, `paste`, or `upload` (PDF/DOCX/TXT/MD,
≤ 10 MB encoded as base64); the server stores the entry and references, fetches
each URL and extracts each document's text synchronously, and returns a
partial-success payload. No quiz/interview generation yet — that's SRA-903 / SRA-904.

## Where things stand right now

- Phase 5/6/7/8 verifications all green; `pytest -m 'not phase8' -q` baseline is clean.
- `StudyEntry` carries no reference material; only the original captured transcript.
- `delete_user_cascade` removes study entries and all descendants in FK-safe order
  (SRA-603); one more loop is needed for the new table.
- The codebase already imports `httpx` for OAuth flows; the URL fetcher will reuse it.
- `pypdf` and `python-docx` are NOT yet in the dependency tree; this ticket adds them.

## What this ticket unlocks

After SRA-901:

```bash
# 1. Plain paste + URL
curl -b 'sra_session=…' -X POST http://127.0.0.1:8000/api/v1/topics \
  -H 'Content-Type: application/json' \
  -d '{
    "topic": "Transformer attention heads",
    "summary": "How attention is computed and why multi-head works",
    "references": [
      {"kind": "url",   "title": "Illustrated transformer", "url": "https://jalammar.github.io/illustrated-transformer/"},
      {"kind": "paste", "title": "Lecture notes",          "pasted_text": "In a transformer, scaled dot-product attention …"}
    ]
  }'
# → 201 with the new entry, references (extracted_text populated), but
#   quiz_set_id / interview_plan_id null.

# 2. With an uploaded PDF (base64-encoded, ≤ 10 MB raw)
curl -b 'sra_session=…' -X POST http://127.0.0.1:8000/api/v1/topics \
  -H 'Content-Type: application/json' \
  -d "{
    \"topic\": \"Latent Dirichlet allocation\",
    \"references\": [
      {\"kind\": \"upload\", \"title\": \"Blei et al. 2003\",
       \"file_name\": \"blei2003.pdf\", \"content_type\": \"application/pdf\",
       \"file_b64\": \"$(base64 -i blei2003.pdf)\"}
    ]
  }"
```

## How this fits into the whole app

```mermaid
sequenceDiagram
    participant U as Browser (SRA-902)
    participant T as POST /api/v1/topics (topics.py)
    participant F as sources.fetcher.fetch_url_text
    participant E as sources.doc_extractor.extract_document_text
    participant R as repository.add_reference / create_entry
    participant DB as reference_materials + study_entries
    U->>T: {topic, summary, references[]}
    T->>R: create_entry + add_reference (paste)
    T->>F: fetch_url_text(url) per URL ref
    T->>E: extract_document_text(bytes, content_type, file_name) per upload ref
    F-->>T: {ok, text} or {ok:false, error}
    E-->>T: {ok, text} or {ok:false, error}
    T->>R: add_reference(...) (url+upload with extracted_text on success)
    R->>DB: INSERT (entry + refs in one tx; fetches/extractions serialized in handler)
    DB-->>R: committed
    R-->>T: entry, references[]
    T-->>U: 201 TopicCreateResult{entry, references[], quiz_set_id=null, interview_plan_id=null, errors[]}
```

Within the server, `sources/fetcher` and `sources/doc_extractor` keep their
external dependencies (`trafilatura`, `pypdf`, `python-docx`) isolated so
SRA-903/SRA-904 just call `repository.get_reference_text_bundle(entry_id, …)`
and never reach the network again.

## Files and why they matter

| File | Action | Reason |
|---|---|---|
| `src/sra/db/models.py` | modify | New `ReferenceMaterial` Base + Create + table=True with 3-way CHECK constraint and FK to `study_entries.id` (ondelete="CASCADE") |
| `src/sra/alembic/versions/f6a7b8c9d0e1_phase9_reference_materials.py` | new | Alembic migration off `e5f6a7b8c9d0` (interview); reverse drops the table |
| `src/sra/sources/__init__.py` | new | Package marker |
| `src/sra/sources/fetcher.py` | new | `fetch_url_text(url, *, timeout_s=8.0, max_bytes=2_000_000) -> FetchResult` with trafilatura |
| `src/sra/sources/doc_extractor.py` | new | `extract_document_text(content, *, content_type, file_name, max_bytes=10_000_000) -> ExtractResult` with pypdf + python-docx dispatch |
| `src/sra/db/repository.py` | modify | `add_reference`, `list_references_for_entry`, `get_reference_text_bundle`, new `ReferenceMaterialsLoop` extension to `delete_user_cascade` |
| `src/sra/api/schemas.py` | modify | `ReferenceCreate` (a discriminated union over `url`/`paste`/`upload`), `ReferenceOut`, `TopicCreate`, `TopicCreateResult` |
| `src/sra/api/routers/topics.py` | new | `POST /api/v1/topics`, `POST /api/v1/topics/{entry_id}/regenerate` (placeholder returns `501 Not Implemented`), `GET /api/v1/entries/{id}/references` |
| `src/sra/api/routers/__init__.py` | modify | Mount `topics.router` under `limit_authenticated` |
| `pyproject.toml` | modify | Add `trafilatura>=1.6,<3`, `pypdf>=5.0,<7`, `python-docx>=1.1,<3` to base deps |
| `requirements-lock.txt` | modify | Pin current versions of all three |
| `tests/unit/test_reference_models.py` | new | CHECK constraint, Base/Create `extra="forbid"` discipline, discriminator validates only the right fields per kind |
| `tests/unit/test_source_fetcher.py` | new | URL: ok, non-200, non-text content-type, malformed → `ok=False` with reason; text-result truncation cap honored |
| `tests/unit/test_doc_extractor.py` | new | TXT/MD/PDF/DOCX happy paths, oversize rejected, empty after extraction rejected, unknown extension rejected, malformed bytes rejected |
| `tests/unit/test_reference_repository.py` | new | Create + list + bundle truncation + cascade + FK behavior |
| `tests/integration/test_phase9_migration.py` | new | Forward + round-trip + replay preserves pre-Phase-9 rows |
| `tests/integration/test_phase9_topic_route.py` | new | 201 happy path with each kind, 422 on bad reference payload, 403 isolation, 401 unauthorized, partial-success on failed URL/extraction |
| `tests/integration/test_route_policy_audit.py` | extend | New routes appear under `user`+`limit_authenticated` |

## TDD starting point

- `src/sra/api/schemas.py::TopicCreate` does not exist.
- `src/sra/db/repository.py::add_reference` raises `NotImplementedError`.
- `src/sra/sources/fetcher.py::fetch_url_text` raises `NotImplementedError`.
- `src/sra/sources/doc_extractor.py::extract_document_text` raises `NotImplementedError`.
- `src/sra/api/routers/topics.py::create_topic_route` raises `NotImplementedError`.
- The Alembic migration is *not* required in step 2; SRA-901 ships the migration in step 3 alongside the rest.

## What to implement

- `ReferenceMaterial` SQLModel (Base/Create/`table=True`) with the 3-way CHECK constraint documented in `docs/plans/2026-10-16-phase9-topic-authoring-and-quality.md` §A. `extra="forbid"` on `ReferenceMaterialCreate`. A `Literal["url", "paste", "upload"]` `kind` field on the Python side.
- Alembic migration `f6a7b8c9d0e1`:
  - `upgrade()`: CREATE TABLE, indices, CHECK constraint.
  - `downgrade()`: DROP TABLE.
- `sra.sources.fetcher.fetch_url_text(url, *, timeout_s=8.0, max_bytes=2_000_000)`:
  - 8 s hard timeout. Rejects `Content-Length > max_bytes` and accumulated body `> max_bytes`.
  - Allowed content types: `text/html`, `text/plain`, `application/xhtml+xml`.
  - Strips `<script>`/`<style>` then runs `trafilatura.extract(...)`.
  - Returns `FetchResult(ok: bool, text: str = '', title: str | None = None, error: str | None = None)`.
- `sra.sources.doc_extractor.extract_document_text(content, *, content_type, file_name, max_bytes=10_000_000)`:
  - Rejects `len(content) > max_bytes`.
  - Dispatches:
    - `text/plain` / `text/markdown` (or `.md`): decode UTF-8 with `errors='replace'`.
    - `application/pdf` (or `.pdf`): `pypdf.PdfReader(io.BytesIO(content)).pages` → join with `\n\n`.
    - `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (or `.docx`): `docx.Document(io.BytesIO(content)).paragraphs` → join.
  - Returns `ExtractResult(ok: bool, text: str = '', error: str | None = None)`. `ok=False` and `error="file_too_large" | "unsupported_content_type" | "empty_after_extraction" | "decode_failed"` per case.
- `repository.add_reference(*, entry_id, kind, title, url=None, pasted_text=None, file_name=None, content_type=None, extracted_text=None, fetch_status='pending', fetch_error=None, session)` — atomic insert; validates the kind-discriminator at the Python layer too.
- `repository.list_references_for_entry(entry_id, *, session)` — order by `created_at`.
- `repository.get_reference_text_bundle(entry_id, *, max_chars_total=12_000, max_chars_per_ref=6_000, session) -> list[tuple[str, str]]` — returns `[(title, excerpt), ...]` ordered by `created_at`; concatenates `extracted_text` (URL/upload rows) and `pasted_text` (paste rows); truncates per-ref with `[... truncated at N chars ...]` so the model knows the bounds.
- Extend `delete_user_cascade` to also drop `reference_materials` for the user's `study_entries` (FK `ondelete="CASCADE"` handles it on Postgres; SQLite has FK enforcement off by default so the explicit loop is required and the test will cover both).
- `POST /api/v1/topics`: validates payload, creates the entry and references, awaits URL fetches and document extractions serially (URL 8 s, doc 8 s soft cap inside the extractor), commits, returns `TopicCreateResult` with `quiz_set_id=None`, `interview_plan_id=None`, and an `errors: list[str]` per failed reference. Refuses to commit if the *entry* topic / subtopic / summary text is blank.
- `POST /api/v1/topics/{entry_id}/regenerate`: stub that returns `501 Not Implemented` in SRA-901 — SRA-902/SRA-903/SRA-904 add the real behaviour.
- `GET /api/v1/entries/{entry_id}/references`: returns `list[ReferenceOut]`, scoped to the caller via `StudyEntry.user_id`.

## Where to look

- `src/sra/agents/ingestion_graph.py::build_ingestion_graph()` — for the model-validation pattern this ticket mirrors in `add_reference`.
- `src/sra/db/repository.py::create_entry` — for the typed insert boundary this ticket extends.
- `src/sra/db/repository.py::delete_user_cascade` — for the explicit-drop loop the new table joins.
- `src/sra/api/routers/entries.py` — for the existing ingest shape the new endpoint sits next to.
- `tickets/SRA-603-demo-reset-job-and-admin-endpoint.md` — cascade contract reference.
- `docs/plans/2026-10-16-phase9-topic-authoring-and-quality.md` — design contract for the model + endpoint shapes.

## Acceptance criteria

- [ ] `POST /api/v1/topics` creates a `StudyEntry` + one `reference_materials` row per paste reference, one row per URL reference with `extracted_text` populated on success or `fetch_error` on failure, and one row per upload reference with the same shape.
- [ ] `POST /api/v1/topics` returns 422 for a reference payload that violates the kind-discriminator (e.g. `kind=url` without `url`, `kind=upload` without `file_b64`) before any DB write.
- [ ] URL fetcher unit tests pass: ok, slow (timeout), oversize, non-200, non-text content-type, malformed URL.
- [ ] Doc extractor unit tests pass: TXT, MD, PDF, DOCX happy paths; oversize rejected; unknown extension / malformed bytes rejected; empty-after-extraction rejected.
- [ ] `delete_user_cascade` removes a user's references in the same FK-safe order; covered by an integration test.
- [ ] Migration round-trip on a Phase 8 database preserves all pre-existing rows.
- [ ] `ruff check .` clean; `pyright` clean on the new modules.
- [ ] `pytest -m 'not phase9' -q` stays at the SRA-509 baseline.
- [ ] `pip check` clean after pinning `trafilatura>=1.6,<3`, `pypdf>=5.0,<7`, `python-docx>=1.1,<3`.

## How to test this ticket alone

```bash
pip install -e '.[dev]'
alembic upgrade head
pytest tests/unit/test_reference_models.py tests/unit/test_source_fetcher.py tests/unit/test_doc_extractor.py tests/unit/test_reference_repository.py -v
pytest tests/integration/test_phase9_migration.py tests/integration/test_phase9_topic_route.py -v
ruff check src/sra pyproject.toml
pyright src/sra
pytest -m 'not phase9' -q   # baseline must stay green
```

## Manual verification

Sign in through the existing `/api/v1/auth/google/login` flow, then:

```bash
curl -b "sra_session=$COOKIE" -X POST http://127.0.0.1:8000/api/v1/topics \
  -H 'Content-Type: application/json' \
  -d '{
    "topic": "Transformer attention heads",
    "summary": "How attention is computed and why multi-head works",
    "references": [
      {"kind": "paste", "title": "Lecture notes", "pasted_text": "Scaled dot-product attention is defined as softmax(QK^T/sqrt(d_k))V"},
      {"kind": "url",   "title": "Bad URL",       "url": "https://localhost:9/no-such-host"}
    ]
  }'
# Expect 201, references with one row fetched and one row fetch_status=failed.
```

Failure/recovery: stop the dev server, hit the route, observe 503 from the rate-limiter / 503 from the upstream URL; restart and confirm the same payload succeeds.

## Verification record

- **Date and environment:** 2026-10-16; Python 3.13.0 (.venv), `trafilatura==2.3.1`, `pypdf==6.21.0`, `python-docx==1.2.0` installed; working directory `/Users/shubhamsingh/Documents/cells/spaced-repetition-agent`.
- **Commands and inputs:**
  - `.venv/bin/python -m pytest tests/unit/test_reference_models.py tests/unit/test_source_fetcher.py tests/unit/test_doc_extractor.py tests/unit/test_reference_repository.py tests/unit/test_topics_schemas.py tests/integration/test_phase9_migration.py tests/integration/test_phase9_topic_route.py tests/integration/test_phase9_cascade.py tests/integration/test_phase9_route_policy_audit.py -v` → **84 passed in 1.49s** (15 + 9 + 12 + 15 + 14 unit + 9 + 7 + 2 + 1 integration, all previously red).
  - `.venv/bin/python -m pytest -m 'not phase9' -q` → **17 failed, 970 passed, 26 skipped, 84 deselected** (the 17 failures are the pre-existing Phase 1–8 limitations; SRA-901 introduced zero new failures, the only ticket-time adjustment was extending `test_route_policy_audit.py`'s policy dict with the three new Phase 9 routes).
  - `.venv/bin/alembic upgrade head` against `sqlite:///./sra.db` → applies `f6a7b8c9d0e1_phase9_reference_materials.py` cleanly; `inspect(engine).get_table_names()` returns the expected 11 tables including `reference_materials` with `ck_reference_materials_kind` CHECK constraint.
  - `.venv/bin/alembic downgrade e5f6a7b8c9d0` then `.venv/bin/alembic upgrade head` → round-trips cleanly; pre-existing rows (users, study_entries, quiz_sets, quiz_attempts) survive both directions.
  - `.venv/bin/python -m ruff check src/sra` → **All checks passed**.
  - `.venv/bin/python -m pyright src/sra/sources src/sra/api/routers/topics.py src/sra/db/models.py src/sra/db/repository.py src/sra/api/schemas.py` → **0 errors, 0 warnings, 0 informations**.
- **New IDs/artifacts and observed downstream results:**
  - New table `reference_materials` with columns `id`, `entry_id` (FK → `study_entries.id`, `ondelete="CASCADE"`, indexed as `ix_reference_materials_entry_id`), `kind`, `title`, `url`, `pasted_text`, `file_name`, `content_type`, `extracted_text`, `fetch_status` (default `"pending"`), `fetch_error`, `fetched_at`, `created_at`; 3-way CHECK constraint `ck_reference_materials_kind` enforcing the kind-discriminator at the SQL layer.
  - New module `src/sra/sources/__init__.py` (package marker), `src/sra/sources/fetcher.py` (HTTP fetch + trafilatura HTML-to-text with `_import_trafilatura` and `_get_httpx_client` module seams), `src/sra/sources/doc_extractor.py` (PDF/DOCX/TXT/MD extraction with `_get_pdf_reader` and `_get_docx_document` module seams).
  - New router `src/sra/api/routers/topics.py` with `POST /api/v1/topics`, `POST /api/v1/topics/{entry_id}/regenerate` (501 stub), `GET /api/v1/entries/{entry_id}/references`, all behind `require_user` + `limit_authenticated`.
  - Migration `f6a7b8c9d0e1_phase9_reference_materials.py` chained off `e5f6a7b8c9d0`; round-trips cleanly.
  - Repository: `add_reference(*, entry_id, kind, title, ..., session)` with full kind-discriminator validation; `list_references_for_entry(*, session)` (ordered by `created_at` ASC); `get_reference_text_bundle(*, session, max_chars_total=12_000, max_chars_per_ref=6_000)` returning `list[tuple[str, str]]` with the exact `"[... truncated at N chars ...]"` marker; `delete_user_cascade` extended with an explicit `reference_materials` loop.
  - API schemas: `ReferenceCreate` (Pydantic-validator discriminated union over `kind`, all field-cross-checks at the Python layer), `ReferenceOut`, `TopicCreate` (`extra="forbid"`, `references: list[ReferenceCreate]` with `min_length=1` so a zero-references payload returns 422 *before* any DB write), `TopicCreateResult` (`entry`, `references`, `quiz_set_id=None`, `interview_plan_id=None`, `errors: list[str]` defaulting to `[]`).
  - `pyproject.toml` adds `trafilatura>=1.6,<3`, `pypdf>=5.0,<7`, `python-docx>=1.1,<3` to base `dependencies`.
  - `requirements-lock.txt` adds `trafilatura==2.3.1`, `pypdf==6.21.0`, `python-docx==1.2.0`, plus the new transitives (`courlan==1.4.0`, `htmldate==1.11.0`, `jusText==3.0.2`, `lxml==6.1.3`, `lxml_html_clean==0.4.5`, plus `babel==2.18.0`, `docstring_parser==0.18.0`).
  - `tests/integration/test_route_policy_audit.py` extended with the three new route entries (`POST /api/v1/topics`, `GET /api/v1/entries/{entry_id}/references`, `POST /api/v1/topics/{entry_id}/regenerate`).
- **Failure/recovery and cleanup:**
  - The Step-2 TDD subagent shipped `test_phase9_migration_downgrade_drops_reference_materials_table` whose body contradicted itself (asserted the table was dropped via `inspect().get_table_names().isdisjoint(...)`, then went on to `SELECT count(*) FROM reference_materials` and expect `0`). Replaced the redundant second query with `pytest.raises(OperationalError)` so the test now correctly pins "after downgrade, the SQL layer cannot SELECT the table". Test now passes cleanly.
  - Implementation subagent ran out of time and did not produce `src/sra/api/routers/topics.py` or register it in `__init__.py`; created both in this session and re-ran the failing integration tests. They pass.
  - `ruff` initially reported one unused import (`ReferenceMaterialCreate` in `repository.py`) and one `getattr` constant warning (`fetcher.py:_extract_title`). Both fixed in this session.
  - `pyright` flagged a missing `binascii` import in `topics.py:decode_upload_bytes`; fixed.
  - The pre-existing `google-genai 2.28.0` ↔ `tenacity` Pip-check conflict (recorded in the SRA-509 baseline) remains unchanged.
- **Automated/manual results, skips, and limitations:**
  - **84/84** new Phase 9 tests pass.
  - **970** regressions pass (matches the SRA-509 baseline); **17** pre-existing failures unchanged in identity and count; **0** new failures introduced by SRA-901.
  - Phase 9 marks: `phase9` registered in `pyproject.toml`'s `[tool.pytest.ini_options].markers` list; `pytest -m 'not phase9' -q` cleanly excludes the 84 new tests.
  - **Limitation 1:** `POST /api/v1/topics/{entry_id}/regenerate` returns 501 (intentional — wired up by SRA-902 / SRA-903 / SRA-904).
  - **Limitation 2:** `quiz_set_id` and `interview_plan_id` are deliberately `null` in `TopicCreateResult` — SRA-903 / SRA-904 fill those in.
  - **Limitation 3:** Real OpenAI live verification against a `_seed`-style DB is still pending; the Step-4 closing for SRA-905 will record that.

## Definition of Done

- [x] All acceptance criteria met
- [x] Migration round-trip + migration replay green
- [x] Cross-user isolation enforced
- [x] New dependency pins committed (`pyproject.toml` + `requirements-lock.txt`)
- [x] Cascade test demonstrates reference rows are removed with the user
- [x] `ruff check src/sra` clean; `pyright` clean on changed modules
- [x] README/PROGRESS/board updated
