# SRA-602 — Dockerfile and docker-compose.yml

**Phase:** 6 — Open-source packaging · **Status:** implemented — 2026-10-14

## Summary

Add a multi-stage `Dockerfile` for the FastAPI + agent service (the macOS desktop client is intentionally not containerized) and a `docker-compose.yml` wiring it to an opt-in Postgres profile and a documented `demo` profile. The default `docker compose up` runs against an on-container SQLite file with no external dependencies, so a clean `git clone` plus `docker compose up` produces a running dashboard on `http://localhost:8000`. The image is identical between the demo and self-host paths: `DEMO_MODE` is a runtime environment flag, not a build-time matrix, so the `demo` profile only documents the variables the hosted demo sets. Nothing inside `src/` changes — the container runs the same FastAPI process, and the entrypoint waits for the database (Postgres profile only), runs `alembic upgrade head`, then execs uvicorn.

## Unlocks

- A first-time self-hoster clones, copies `.env.example` to `.env`, fills `OPENAI_API_KEY` plus the Google OAuth secrets, and runs `docker compose up`; the dashboard answers on `http://localhost:8000/api/health` and `http://localhost:8000/docs`.
- The public-demo operator adds `DEMO_MODE=true` and `DEMO_DATA_RETENTION_DAYS=7`, then runs `docker compose --profile demo up` for a separate instance with tighter rate limits and nightly study-data wipes.
- A second self-hoster swaps SQLite for Postgres with `DATABASE_URL=postgresql://…` plus `docker compose --profile postgres up`; the same image runs against a real database without a code change.

## Files

| File | Change | Why |
|---|---|---|
| [`Dockerfile`](../Dockerfile) | new | Multi-stage `python:3.13-slim-bookworm`: the `builder` stage installs `build-essential` + `libpq-dev`, then `pip install --no-cache-dir -r requirements-lock.txt` and `pip install --no-cache-dir -e .`; the `runtime` stage copies site-packages, `src/`, `alembic.ini`, and `docker/entrypoint.sh`, sets `WORKDIR /app`, `USER app`, `EXPOSE 8000`, `ENTRYPOINT ["/app/docker/entrypoint.sh"]`, and `CMD ["uvicorn", "sra.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]`. |
| [`docker/entrypoint.sh`](../docker/entrypoint.sh) | new | POSIX `sh` with `set -eu`. When `DATABASE_URL` starts with `postgresql://` or `postgres://` and the host is not `localhost`/`127.0.0.1`/`::1`, it waits up to 60 s for the TCP port; then `alembic upgrade head`; then `exec "$@"`. Made executable at build time (`chmod +x`). |
| [`.dockerignore`](../.dockerignore) | new | Repository-root build-context exclusion: `.venv`, `.git`, `.env`, `tests`, `docs`, `dashboard`, `*.db`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.kilo`, `*.pyc`. This is the file that keeps `__pycache__/` and `.pytest_cache/` out of the image — there is no `docker/.gitignore`, and none is needed. |
| [`docker-compose.yml`](../docker-compose.yml) | new | `version: "3.9"`. Default `app` service (`build: .`, `env_file: .env`, `DATABASE_URL: sqlite:////app/data/sra.db`, `ports: ["8000:8000"]`, `volumes: ["sra-data:/app/data"]`, `healthcheck` on `http://127.0.0.1:8000/api/health`); opt-in `postgres` service (`postgres:16-alpine`, `POSTGRES_DB: sra`, `POSTGRES_USER: sra`, `POSTGRES_PASSWORD: sra-dev`, `volumes: ["sra-postgres:/var/lib/postgresql/data"]`); opt-in `demo` service extending `app` with `DEMO_MODE: "true"`, `DEMO_DATA_RETENTION_DAYS: "7"`, `ports: ["8001:8000"]`, `volumes: ["sra-data-demo:/app/data"]`. Named volumes `sra-data`, `sra-data-demo`, `sra-postgres`. |
| [`tests/integration/test_docker_compose_config.py`](../tests/integration/test_docker_compose_config.py) | new | Parses `docker-compose.yml` with PyYAML and asserts `services.app` exists with `env_file: .env`, the `postgres` and `demo` profiles exist and `demo` sets `DEMO_MODE=true`, every env value is a string or list of strings, and the compose schema version is `3.8` or `3.9`. |
| [`tests/integration/test_dockerfile.py`](../tests/integration/test_dockerfile.py) | new | Static analysis of the `Dockerfile`: `FROM … AS builder`, a single `pip install` in the builder, installed packages copied to the runtime stage, a non-root `USER app`, and an `ENTRYPOINT`/`CMD` chain invoking uvicorn with the factory pattern. Skips cleanly when `docker` is not on `PATH`. |

## Acceptance criteria

- [x] `Dockerfile` builds a Python 3.13 image with a builder + runtime split, runs as a non-root user, exposes `8000`, and `CMD` invokes uvicorn with the `create_app` factory
- [x] `docker/entrypoint.sh` is executable, idempotent, waits for Postgres when applicable, runs `alembic upgrade head`, then execs the CMD
- [x] `docker-compose.yml` has a default `app` service, an opt-in `postgres` profile, and an opt-in `demo` profile that sets `DEMO_MODE=true`
- [x] `.dockerignore` excludes `.venv`, `.git`, `.env`, `tests`, `dashboard`, `*.db`, and the Python `__pycache__` directories
- [x] No secret values are hard-coded in `docker-compose.yml`; the `env_file: .env` mechanism remains the only config injection path
- [x] `tests/integration/test_docker_compose_config.py` and `tests/integration/test_dockerfile.py` pass against the new files
- [x] All Phase 1/2/3/4/5 tests remain green; Ruff and Pyright pass with no new warnings
- [x] Build-context exclusion lives in the repository-root `.dockerignore`; no `docker/.gitignore` is shipped and no `docker/`-scoped ignore file is required

## Tests

```bash
pytest tests/integration/test_dockerfile.py tests/integration/test_docker_compose_config.py -v
```

These are parser-only checks and need no Docker daemon; they skip cleanly when `docker` is absent from `PATH`. No test carries the `phase6` marker, so `pytest -m 'not phase6' -q` selects the whole 1261-test suite rather than Phases 1–5 — see [`tests/README.md`](../tests/README.md).

## Manual verification

- [ ] `docker build -t sra:phase6 .` succeeds.
- [ ] `cp -i .env.example .env` (fill in `OPENAI_API_KEY`), then `docker compose up`, then `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/health` returns `200`; finish with `docker compose down -v`.
- [ ] `docker compose --profile postgres up -d` then `docker compose exec -T app alembic current` reports the latest revision; finish with `docker compose --profile postgres down -v`.
- [ ] `docker compose --profile demo config -q` and `docker compose config -q` both parse cleanly.

**Verification:**

- **Date and environment:** 2026-10-14, macOS command runner (static analysis only; no Docker daemon invocation).
- **Commands:** `pytest tests/integration/test_dockerfile.py tests/integration/test_docker_compose_config.py -v`.
- **Result:** **23 passed, 0 failed, 0 skipped** (11 Dockerfile + 12 docker-compose).
- **Artifacts:** the four new files listed above; the entrypoint was made executable with `chmod +x`.
- **Failure/recovery:** no failures encountered during the static-analysis run.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
