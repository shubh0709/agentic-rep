# SH-014 — Playwright harness is not installable from dev dependencies

**Type:** Test infrastructure bug
**Priority:** High
**Status:** Fixed
**Affected feature:** Real-browser regression harness
**Baseline revision:** `754274b`

## User impact

A contributor following the documented `pip install -e ".[dev]"` setup cannot
run `tests/playwright/`: the Python Playwright package is undeclared, Chromium
installation and the opt-in command are undocumented, and the lockfile cannot
recreate the environment used by the suite.

## Preconditions and reproduction

1. Create a clean virtual environment.
2. Run `pip install -e ".[dev]"`.
3. Set `SRA_RUN_PLAYWRIGHT_TESTS=1` and collect the Playwright suite.

**Expected:** The declared development environment contains the Python browser
driver, and the test guide explains the one-time Chromium install and run
command.

**Actual:** `playwright` is absent unless installed out of band; neither the
package nor its `greenlet` and `pyee` dependencies are pinned in the lockfile.

## Root cause

The opt-in tests were added without updating the development dependency group,
reproducible lock, or the repository-owned test command guide.

## Acceptance criteria

- `playwright` is declared in the `dev` extra.
- The known-working Playwright dependency set is pinned in
  `requirements-lock.txt`.
- `tests/README.md` documents browser installation, activation, and the exact
  suite command.
- The documented command runs all browser scenarios and returns a failing exit
  status on regressions.

## Verification record

- Environment inspection on 2026-10-24: Playwright 1.64.0 existed in the
  working virtualenv but not in `pyproject.toml` or `requirements-lock.txt`.
- `SRA_RUN_PLAYWRIGHT_TESTS=1 pytest tests/playwright/test_user_flows.py -q`:
  22 passed after the application repairs.
- Fresh temporary virtualenv install with `-e ".[dev]" -c requirements-lock.txt`
  installed Playwright 1.64.0; `pip check` reported no broken requirements.
