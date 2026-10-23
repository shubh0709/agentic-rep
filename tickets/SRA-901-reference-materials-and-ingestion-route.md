# SRA-901 — Reference material schema, URL fetcher, doc extractor, ingestion route

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** not started

## Summary

Adds the data model, ingestion route, URL fetcher, and document extractor the rest of Phase 9 builds on. An authenticated user can `POST /api/v1/topics` with a topic plus references whose `kind` is one of `url`, `paste`, or `upload` (PDF/DOCX/TXT/MD, ≤ 10 MB base64); the server stores the entry and references, fetches each URL and extracts each document's text synchronously, and returns a partial-success payload. No quiz/interview generation yet — that is SRA-903 / SRA-904. `sources/fetcher` and `sources/doc_extractor` isolate their external dependencies (`trafilatura`, `pypdf`, `python-docx`) so SRA-903 / SRA-904 just call `repository.get_reference_text_bundle(entry_id, …)` and never reach the network again.

## Unlocks

- SRA-902 has a working endpoint to hang the dashboard's "Create topic with references" UI on; SRA-903 and SRA-904 gain a ground-truth text bundle to ground questions and interview outlines in.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/db/models.py` | edit | `ReferenceMaterial` Base / Create / `table=True`, 3-way CHECK, FK to `study_entries.id` (`ondelete="CASCADE"`). |
| `src/sra/alembic/versions/f6a7b8c9d0e1_phase9_reference_materials.py` | new | Chained off `e5f6b8c9d0`; `upgrade()` creates table/indices/CHECK, `downgrade()` drops the table. |
| `src/sra/sources/__init__.py`, `fetcher.py`, `doc_extractor.py` | new | Package marker; `fetch_url_text(url, *, timeout_s=8.0, max_bytes=2_000_000) -> FetchResult`; `extract_document_text(content, *, content_type, file_name, max_bytes=10_000_000) -> ExtractResult`. |
| `src/sra/db/repository.py` | edit | `add_reference`, `list_references_for_entry`, `get_reference_text_bundle`, and a `reference_materials` loop in `delete_user_cascade`. |
| `src/sra/api/schemas.py` | edit | `ReferenceCreate` (union over `url`/`paste`/`upload`), `ReferenceOut`, `TopicCreate`, `TopicCreateResult`. |
| `src/sra/api/routers/topics.py` | new | `POST /api/v1/topics`; `POST /api/v1/topics/{entry_id}/regenerate` (501 placeholder); `GET /api/v1/entries/{entry_id}/references`. |
| `src/sra/api/routers/__init__.py` | edit | Mount `topics.router` under `limit_authenticated`. |
| `pyproject.toml`, `requirements-lock.txt` | edit | Add `trafilatura>=1.6,<3`, `pypdf>=5.0,<7`, `python-docx>=1.1,<3` and pin them; register the `phase9` marker. |
| `tests/unit/test_reference_models.py`, `test_source_fetcher.py`, `test_doc_extractor.py`, `test_reference_repository.py` | new | CHECK + `extra="forbid"`; URL ok / non-200 / non-text / malformed; TXT/MD/PDF/DOCX plus oversize, empty, unknown-extension, malformed-bytes rejection; create + list + bundle truncation + cascade + FK. |
| `tests/integration/test_phase9_migration.py`, `test_phase9_topic_route.py`, `test_route_policy_audit.py` | new / edit | Round-trip + replay preserves pre-Phase-9 rows; 201 per kind, 422 bad payload, 403, 401, partial success; new routes under `user` + `limit_authenticated`. |

## Design notes

- **Fetcher:** 8 s hard timeout; rejects `Content-Length > max_bytes` and accumulated body `> max_bytes`; allowed types `text/html`, `text/plain`, `application/xhtml+xml`; strips `<script>`/`<style>` before `trafilatura.extract(...)`. Returns `FetchResult(ok, text='', title=None, error=None)`.
- **Extractor:** rejects `len(content) > max_bytes`; dispatches `text/plain` / `text/markdown` → UTF-8 with `errors='replace'`; `application/pdf` → `pypdf.PdfReader(...).pages` joined with `\n\n`; DOCX OpenXML → `docx.Document(...).paragraphs` joined. Returns `ExtractResult(ok, text='', error=None)`, `error` being one of `file_too_large` / `unsupported_content_type` / `empty_after_extraction` / `decode_failed`.
- **Bundle:** `get_reference_text_bundle(entry_id, *, max_chars_total=12_000, max_chars_per_ref=6_000, session)` returns `[(title, excerpt), ...]` ordered by `created_at`, concatenating `extracted_text` (url/upload) and `pasted_text` (paste), truncating per-ref with `[... truncated at N chars ...]`. `TopicCreate.references` has `min_length=1` so a zero-reference payload 422s *before* any DB write.

## Acceptance criteria

- [ ] `POST /api/v1/topics` creates a `StudyEntry` + one `reference_materials` row per paste reference, one row per URL reference with `extracted_text` populated on success or `fetch_error` on failure, and one row per upload reference with the same shape
- [ ] `POST /api/v1/topics` returns 422 for a reference payload that violates the kind-discriminator (e.g. `kind=url` without `url`, `kind=upload` without `file_b64`) before any DB write
- [ ] URL fetcher unit tests pass: ok, slow (timeout), oversize, non-200, non-text content-type, malformed URL
- [ ] Doc extractor unit tests pass: TXT, MD, PDF, DOCX happy paths; oversize rejected; unknown extension / malformed bytes rejected; empty-after-extraction rejected
- [ ] `delete_user_cascade` removes a user's references in the same FK-safe order; covered by an integration test
- [ ] Migration round-trip on a Phase 8 database preserves all pre-existing rows
- [ ] `ruff check .` clean; `pyright` clean on the new modules
- [ ] `pytest -m 'not phase9' -q` stays at the SRA-509 baseline
- [ ] `pip check` clean after pinning `trafilatura>=1.6,<3`, `pypdf>=5.0,<7`, `python-docx>=1.1,<3`

## Tests

```bash
pip install -e '.[dev]'
alembic upgrade head
pytest tests/unit/test_reference_models.py tests/unit/test_source_fetcher.py tests/unit/test_doc_extractor.py tests/unit/test_reference_repository.py tests/unit/test_topics_schemas.py tests/integration/test_phase9_migration.py tests/integration/test_phase9_topic_route.py tests/integration/test_phase9_cascade.py tests/integration/test_phase9_route_policy_audit.py -v
pytest -m 'not phase9' -q
ruff check src/sra
pyright src/sra/sources src/sra/api/routers/topics.py src/sra/db/models.py src/sra/db/repository.py src/sra/api/schemas.py
pip check
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

- [ ] Confirm 201 with one fetched row and one `fetch_status=failed` row; then stop the dev server, hit the route, observe the 503, restart, and confirm the same payload succeeds.

**Verification (2026-10-16):**

- Environment: Python 3.13.0 (`.venv`), `trafilatura==2.3.1`, `pypdf==6.21.0`, `python-docx==1.2.0`.
- Focused → **84 passed in 1.49s** (15 + 9 + 12 + 15 + 14 unit, then 9 + 7 + 2 + 1 integration), all previously red.
- `pytest -m 'not phase9' -q` → **17 failed, 970 passed, 26 skipped, 84 deselected** — the 17 are pre-existing Phase 1–8 limitations; **zero** new failures. Only ticket-time adjustment: extending `test_route_policy_audit.py`'s policy dict with the three new routes.
- `alembic upgrade head` on `sqlite:///./sra.db` applies `f6a7b8c9d0e1_phase9_reference_materials.py` cleanly; `inspect(engine).get_table_names()` returns 11 tables including `reference_materials` with `ck_reference_materials_kind`. `downgrade e5f6a7b8c9d0` then `upgrade head` round-trips cleanly; pre-existing users / study_entries / quiz_sets / quiz_attempts rows survive both directions.
- `ruff check src/sra` → All checks passed. `pyright` on the new modules → **0 errors, 0 warnings, 0 informations**.
- New table columns: `id`, `entry_id` (FK → `study_entries.id`, `ondelete="CASCADE"`, indexed `ix_reference_materials_entry_id`), `kind`, `title`, `url`, `pasted_text`, `file_name`, `content_type`, `extracted_text`, `fetch_status` (default `"pending"`), `fetch_error`, `fetched_at`, `created_at`. Module seams added for testability: `_import_trafilatura`, `_get_httpx_client`, `_get_pdf_reader`, `_get_docx_document`.
- Locks: `trafilatura==2.3.1`, `pypdf==6.21.0`, `python-docx==1.2.0` plus transitives `courlan==1.4.0`, `htmldate==1.11.0`, `jusText==3.0.2`, `lxml==6.1.3`, `lxml_html_clean==0.4.5`, `babel==2.18.0`, `docstring_parser==0.18.0`.
- Fixes during the pass: a self-contradicting `test_phase9_migration_downgrade_drops_reference_materials_table` now uses `pytest.raises(OperationalError)`; an unused `ReferenceMaterialCreate` import and a `getattr` constant warning in `fetcher.py:_extract_title` were fixed; a missing `binascii` import in `topics.py:decode_upload_bytes` was added. `topics.py` and its `__init__.py` registration were completed in the verification session after the implementation subagent ran out of time.
- **Limitations:** (1) `POST /api/v1/topics/{entry_id}/regenerate` returns 501 by design. (2) `quiz_set_id` / `interview_plan_id` are deliberately `null` in `TopicCreateResult`. (3) Real OpenAI live verification is pending and rolls into SRA-905. The pre-existing `google-genai 2.28.0` ↔ `tenacity` `pip check` conflict (SRA-509 baseline) is unchanged.
- > **Note:** the status line still reads `Planned`.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).