# SRA-604 — Self-host documentation and demo-mode quickstart

**Phase:** 6 — Open-source packaging · **Status:** implemented — 2026-10-14

## Summary

Rewrite the top of `README.md` so a first-time visitor sees the project description, the public-demo link, and the three relevant on-ramps: self-host with Docker, self-host from source, and try the public demo. Add a dedicated **Self-hosting with Docker** section, a **Demo mode** section, a **Google OAuth setup** section, and an updated **Environment variables** table. Update `.env.example` with the Phase 6 variables `DEMO_MODE`, `DEMO_DATA_RETENTION_DAYS`, and `DEMO_RESET_INTERVAL_S`, and update `tickets/README.md` and `PROGRESS.md` to reflect the closed Phase 5 status and the open / in-progress Phase 6 status. The macOS-only desktop notice stays exactly where it already lives, and existing Phase 1–5 sections that still describe accurate behaviour are not rewritten — the new content is additive. The README does not invent facts: every command it claims is already exercised by SRA-602's manual verification, every env var it lists is in `.env.example`, and every endpoint it links to exists in SRA-603's admin router. No new code lives in this ticket — only prose plus the board and progress updates.

## Unlocks

- A visitor finds the public demo, the self-host quickstart, and the demo-mode path in the first scroll, without reading `docs/plans/...` or the design doc.
- A new self-hoster completes `git clone` → `cp -i .env.example .env` → `docker compose up` and reaches a running dashboard on `http://localhost:8000`.
- SRA-605 can check the README's claims against the real files with the content tests.

## Files

| File | Change | Why |
|---|---|---|
| [`README.md`](../README.md) | edit | Adds a one-paragraph **Public demo** callout near the top (`https://demo.spaced-repetition-agent.example`, tighter rate limits, 7-day retention, no real study data), a **Self-hosting with Docker** section (clone, `cp -i .env.example .env`, `docker compose up`), a **Demo mode** subsection (`docker compose --profile demo up`), a **Google OAuth setup** section, an updated **Environment variables** table covering Phase 1–6 knobs, and the Phase 6 row in the phase checklist. Phase 1–5 sections are only inserted into or reordered. |
| [`.env.example`](../.env.example) | edit | Appends a `# --- Phase 6: open-source packaging ---` block with `DEMO_MODE=false`, `DEMO_DATA_RETENTION_DAYS=7`, `DEMO_RESET_INTERVAL_S=3600`, each with a one-line comment. |
| [`tickets/README.md`](../tickets/README.md) | edit | Adds a `## Board — Phase 6 (Open-source packaging)` section after the Phase 5 board using the same table format: ticket, title, what it adds. Mirrors the Phase 5 style with a dependency diagram and a per-ticket responsibility table. |
| [`PROGRESS.md`](../PROGRESS.md) | edit | Replaces the Phase 6 stub line (`Not started. Dockerfile, docker-compose.yml, DEMO_MODE + src/sra/jobs/demo_reset.py, final README self-host instructions.`) with an `Implemented — 2026-10-14` entry plus a short narrative summarizing SRA-601–605, matching the Phase 5 closing style. |

## Acceptance criteria

- [ ] `README.md` lists the public-demo URL, the self-host quickstart, and the demo-mode path within the first scroll
- [ ] `.env.example` includes `DEMO_MODE`, `DEMO_DATA_RETENTION_DAYS`, `DEMO_RESET_INTERVAL_S` with inline defaults and one-line descriptions
- [ ] `tickets/README.md` includes a Phase 6 board with the same table format as Phase 5 and a dependency Mermaid diagram
- [ ] `PROGRESS.md` updates the Phase 6 entry from "Not started" to a Phase-5-style closing summary
- [ ] `tests/unit/test_readme_content.py` and `tests/unit/test_env_example_content.py` (added by the test subagent in Step 2) pass
- [ ] All Phase 1/2/3/4/5 tests remain green; Ruff and Pyright pass with no new warnings

## Tests

```bash
pytest tests/unit/test_readme_content.py tests/unit/test_env_example_content.py -v
```

These are content checks over the Markdown and `.env.example`, not behavioural tests — there is no runtime contract to exercise. No test carries the `phase6` marker, so `pytest -m 'not phase6' -q` selects the whole 1261-test suite rather than Phases 1–5; see [`tests/README.md`](../tests/README.md).

## Manual verification

- [ ] `grep -nE '## (Public demo|Self-hosting|Demo mode|Google OAuth|Environment variables)' README.md` returns non-empty matches, and the new sections appear in that order.
- [ ] `grep -nE 'DEMO_MODE|DEMO_DATA_RETENTION_DAYS|DEMO_RESET_INTERVAL_S' .env.example` returns non-empty matches.
- [ ] `docker compose --help | grep -E 'profile'` matches — the demo flag exists in modern Compose.
- [ ] The public-demo placeholder round-trips: `sed -i '' 's|https://demo.spaced-repetition-agent.example|TEST_ONLY|' README.md`, `grep -E 'TEST_ONLY' README.md` matches, then `sed -i '' 's|TEST_ONLY|https://demo.spaced-repetition-agent.example|' README.md` restores it.

**Verification:**

- **Date and environment:** 2026-10-14, macOS, Python 3.13.
- **Result:** `PROGRESS.md` records SRA-604/603 as "demo callout, self-hosting guide, env-var table, `.env.example`; 17 doc + **24** tests".
- **Known caveat:** this ticket's own verification-record block was never filled in — the line above is the phase-level entry, not a per-command log.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
