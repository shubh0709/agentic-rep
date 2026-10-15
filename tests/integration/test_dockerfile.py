"""SRA-602 — static structural validation of the ``Dockerfile``.

Pure text inspection -- no Docker daemon required. Skips when ``docker`` is
missing from ``PATH`` (the optional smoke check) and the Dockerfile is not
present, so a developer without Docker still gets useful signal from the
rest of the suite.
"""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = ROOT / "Dockerfile"
DOCKERIGNORE_PATH = ROOT / ".dockerignore"


@pytest.fixture(scope="module")
def dockerfile_text():
    if not DOCKERFILE_PATH.exists():
        pytest.skip("Dockerfile is not present yet")
    return DOCKERFILE_PATH.read_text()


@pytest.fixture(scope="module")
def dockerignore_text():
    if not DOCKERIGNORE_PATH.exists():
        return ""
    return DOCKERIGNORE_PATH.read_text()


def test_dockerfile_uses_multi_stage_build(dockerfile_text):
    """The runtime image must be a second ``FROM`` so build tools do not leak into it."""
    from_lines = [
        line.strip() for line in dockerfile_text.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert len(from_lines) >= 2, f"expected a multi-stage build, got {from_lines!r}"
    assert any("AS" in line.upper() for line in from_lines), (
        "at least one FROM must declare a named stage (e.g. ``FROM ... AS builder``)"
    )


def test_dockerfile_targets_python_3_13(dockerfile_text):
    """Pin Python 3.13 so the runtime matches ``.python-version`` and ``pyproject.toml``."""
    assert "python:3.13" in dockerfile_text


def test_dockerfile_runs_as_non_root_user(dockerfile_text):
    """A non-root ``USER app`` (or similar) keeps the runtime container from running as UID 0."""
    lines = [line.strip() for line in dockerfile_text.splitlines()]
    user_lines = [line for line in lines if line.upper().startswith("USER ")]
    assert user_lines, "Dockerfile must declare a USER directive (non-root)"
    user_value = user_lines[-1].split(maxsplit=1)[1].strip()
    assert user_value and user_value != "root", f"USER must not be root, got {user_value!r}"


def test_dockerfile_exposes_port_8000(dockerfile_text):
    assert any(
        line.strip().upper().startswith("EXPOSE ") and "8000" in line
        for line in dockerfile_text.splitlines()
    ), "Dockerfile must EXPOSE 8000"


def test_dockerfile_cmd_invokes_uvicorn_with_create_app_factory(dockerfile_text):
    """The CMD must use uvicorn's ``--factory`` flag against ``sra.api.app:create_app``."""
    lowered = dockerfile_text.lower()
    assert "uvicorn" in lowered, "CMD must invoke uvicorn"
    assert "create_app" in lowered, "CMD must reference the create_app factory"
    assert "--factory" in lowered, "CMD must use uvicorn's --factory flag"


def test_dockerfile_copies_src_for_runtime_stage(dockerfile_text):
    """The runtime stage must COPY the source tree so the package is importable."""
    images: list[list[str]] = [[]]
    current = images[0]
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            current = []
            images.append(current)
        current.append(line)
    runtime_stage = "\n".join(images[-1])
    assert "COPY" in runtime_stage, "runtime stage must COPY the application source tree"


def test_dockerfile_pip_installs_requirements_lock(dockerfile_text):
    """The builder must install from the locked requirements file, not a free-floating ``pip install sra``."""
    assert "requirements-lock.txt" in dockerfile_text, (
        "builder stage must install from requirements-lock.txt so the runtime is reproducible"
    )


def test_dockerfile_runs_entrypoint_or_inlines_entrypoint_logic(dockerfile_text):
    """An ENTRYPOINT that calls the wait-for-db / alembic / uvicorn chain lives at the top."""
    assert any(
        line.strip().upper().startswith("ENTRYPOINT ") for line in dockerfile_text.splitlines()
    ), "Dockerfile must declare an ENTRYPOINT (the entrypoint script wraps uvicorn)"


# ---------------------------------------------------------------------------
# .dockerignore
# ---------------------------------------------------------------------------


def test_dockerignore_excludes_secrets_and_build_artifacts(dockerignore_text):
    """``.env``, ``.venv``, ``.git``, and ``tests`` must not be copied into the image."""
    for needle in (".env", ".venv", ".git", "tests"):
        assert needle in dockerignore_text, f".dockerignore must exclude {needle!r}"


def test_dockerignore_excludes_python_cache(dockerignore_text):
    """__pycache__ / .pytest_cache must not bloat the build context."""
    assert "__pycache__" in dockerignore_text or ".pytest_cache" in dockerignore_text


def test_dockerignore_excludes_local_database_files(dockerignore_text):
    assert "*.db" in dockerignore_text, ".dockerignore must exclude *.db"


# ---------------------------------------------------------------------------
# Optional Docker CLI smoke check
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available on PATH")
def test_docker_cli_is_available_when_present():
    """Sanity check: confirm the Docker CLI is callable when this test runs."""
    assert shutil.which("docker") is not None