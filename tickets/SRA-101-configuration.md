# SRA-101 — Application configuration

**Phase:** 1 — Core loop · **Status:** implemented

## Summary

`get_settings()` is the one function every other component reads configuration
through. The DB engine reads `Settings.database_url`; the LangGraph agents read
`Settings.openai_model` and the OpenAI key. The `Settings` pydantic-settings
class is already written for you — this ticket only implements `get_settings()`
by constructing `Settings()` and making that construction a cached singleton, so
one validated object is shared by every route, job, and test.

## Unlocks

- SRA-105 and SRA-106 can read `OPENAI_API_KEY` and `openai_model` from one
  shared instance instead of constructing settings ad hoc.
- The DB engine and every API route read `Settings.database_url` off the same
  validated object.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/config/`](../src/sra/config/) | edit | `get_settings() -> Settings` returns a `Settings()` built by the `@lru_cache` decorator already applied above it. |
| [`tests/unit/test_config.py`](../tests/unit/test_config.py) | run | 3 scaffold-supplied tests covering the cache and the missing-key path. |

## Acceptance criteria

- [ ] `get_settings()` returns a `Settings` instance built from environment
  variables (and `.env` via pydantic-settings' `env_file` config, already set up
  in `model_config`)
- [ ] Calling `get_settings()` twice in the same process returns the *same
  object* (`is`, not just `==`)
- [ ] A missing `OPENAI_API_KEY` causes `get_settings()` to raise (pydantic's
  validation error is enough — don't add your own check)

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_config.py -v
```

Expected once implemented: 3 passed. At the scaffold, 2 fail with
`NotImplementedError` and 1 (`test_settings_missing_required_key_raises`) passed
for the wrong reason — `NotImplementedError` also satisfies
`pytest.raises(Exception)`. Rerun that one alone to confirm it still passes for
the *right* reason, a real `pydantic.ValidationError` about the missing field.

## Manual verification

- [ ] This prints the three config values, then `True` — the cache returning one
      instance:

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

- [ ] `pytest -q` shows no new failures elsewhere. Other modules still fail on
      their own unimplemented stubs until their tickets land.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).