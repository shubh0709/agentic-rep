# SRA-101 — Application configuration

**Type:** Task
**Phase:** 1 — Core loop
**Priority:** Highest — nothing else can run without it
**Blocked by:** none
**Blocks:** SRA-105, SRA-106

## Summary

Implement `get_settings()`, the one small stub in `src/sra/config.py`. Everything else (the DB engine, the LangGraph agents' OpenAI calls) reads config through this function, so it's the natural first ticket.

## Files

- `src/sra/config.py`

## What to implement

- `get_settings() -> Settings` — construct and return a `Settings()` instance. `Settings` itself (the pydantic-settings class with all the fields) is already written for you; this function just needs to instantiate it and make that instantiation a cached singleton.

Read the full docstring in the file first — it names the exact pattern (`functools.lru_cache`, already imported and applied as a decorator) and explains why a cached singleton matters here.

## Acceptance criteria

- [ ] `get_settings()` returns a `Settings` instance built from environment variables (and `.env` via pydantic-settings' `env_file` config, already set up in `model_config`)
- [ ] Calling `get_settings()` twice in the same process returns the *same object* (`is`, not just `==`)
- [ ] A missing `OPENAI_API_KEY` causes `get_settings()` to raise (pydantic's validation error is enough — don't add your own check)

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_config.py -v
```

Expected once implemented: 3 passed. Currently: 2 fail with `NotImplementedError`, 1 (`test_settings_missing_required_key_raises`) passes today for the wrong reason — `NotImplementedError` happens to satisfy `pytest.raises(Exception)` too. After you implement this, rerun that one test on its own and make sure it's still passing for the *right* reason (a real `pydantic.ValidationError` about the missing field, not a coincidence):

```bash
pytest tests/unit/test_config.py::test_settings_missing_required_key_raises -v
```

## Manual verification

```bash
python3 -c "
from sra.config import get_settings
import os
os.environ['OPENAI_API_KEY'] = 'sk-test'
s = get_settings()
print(s.openai_model, s.database_url, s.whisper_model_size)
print(get_settings() is s)
"
```

Should print the three config values, then `True`.

## Definition of Done

- [ ] All acceptance criteria met
- [ ] `pytest tests/unit/test_config.py -v` — 3 passed
- [ ] Full suite doesn't regress elsewhere: `pytest -q` (other modules will still fail on their own unimplemented stubs — that's expected until their tickets are done)
