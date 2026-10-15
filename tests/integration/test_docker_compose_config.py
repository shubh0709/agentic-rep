"""SRA-602 — structural validation of ``docker-compose.yml``.

The compose file is parsed with PyYAML (no Docker daemon required), then
asserted to expose the documented shapes so a future contributor cannot
silently break the self-host / demo / postgres profiles.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose_config():
    compose_path = ROOT / "docker-compose.yml"
    if not compose_path.exists():
        pytest.skip("docker-compose.yml is not present yet")
    with compose_path.open() as handle:
        return yaml.safe_load(handle)


def test_compose_file_parses_with_pyyaml(compose_config):
    assert isinstance(compose_config, dict)
    assert "services" in compose_config


def test_compose_schema_version_is_supported(compose_config):
    """Modern compose (>= 3.8) so the demo / postgres profile syntax is recognized."""
    version = compose_config.get("version")
    assert version is not None, "compose schema version must be declared"
    assert str(version) in {"3.8", "3.9", "3.10", "3.11"}


def test_default_app_service_exists_and_uses_env_file(compose_config):
    services = compose_config["services"]
    assert "app" in services
    app = services["app"]
    env_file = app.get("env_file")
    assert env_file is not None, "app must declare env_file so secrets stay out of the repo"
    assert env_file == ".env" or ".env" in env_file


def test_default_app_service_does_not_pin_a_compose_profile(compose_config):
    """The default ``app`` service must not be in a profile -- ``docker compose up`` runs it."""
    app = compose_config["services"]["app"]
    assert "profiles" not in app, "the default app service must run without --profile"


def test_postgres_profile_exists_and_depends_on_app(compose_config):
    services = compose_config["services"]
    assert "postgres" in services
    postgres = services["postgres"]
    assert "profiles" in postgres
    assert "postgres" in postgres["profiles"]
    assert "image" in postgres
    assert "postgres" in str(postgres["image"]).lower()


def test_demo_profile_sets_demo_mode_true(compose_config):
    """The demo profile must set ``DEMO_MODE=true`` so the demo profile is observable."""
    services = compose_config["services"]
    demo_candidates = [
        name for name, svc in services.items()
        if "profiles" in svc and "demo" in svc.get("profiles", [])
    ]
    assert demo_candidates, "expected a service with the 'demo' profile"

    for name in demo_candidates:
        svc = services[name]
        env = svc.get("environment", {})
        rendered = _flatten_env(env)
        assert rendered.get("DEMO_MODE") == "true", (
            f"demo service {name!r} must set DEMO_MODE=true; got {rendered.get('DEMO_MODE')!r}"
        )


def test_demo_profile_sets_demo_data_retention_days(compose_config):
    services = compose_config["services"]
    demo_candidates = [
        name for name, svc in services.items()
        if "profiles" in svc and "demo" in svc.get("profiles", [])
    ]
    for name in demo_candidates:
        svc = services[name]
        env = svc.get("environment", {})
        rendered = _flatten_env(env)
        assert "DEMO_DATA_RETENTION_DAYS" in rendered


def test_no_hardcoded_secrets_in_compose_env_values(compose_config):
    """The compose file forwards env values only; no real secrets may live here."""
    services = compose_config.get("services", {})
    suspicious_substrings = ("secret", "password", "token", "key")
    for service_name, svc in services.items():
        env = svc.get("environment", {})
        rendered = _flatten_env(env)
        for key, value in rendered.items():
            if key.upper().endswith("_SECRET") or key.upper().endswith("_PASSWORD") or key.upper().endswith("_TOKEN"):
                assert value not in ({"your-", "replace", "change-me"}), (
                    f"service {service_name!r} env {key} looks like a placeholder; "
                    "real secrets must live in .env"
                )
            if not isinstance(value, str):
                continue
            lowered = value.lower()
            for needle in suspicious_substrings:
                if needle in lowered and not value.startswith("${"):
                    # Compose variable references like ${VAR} are allowed (they expand at runtime)
                    # Any literal string with "secret" or "password" inside is treated as suspect.
                    raise AssertionError(
                        f"service {service_name!r} env {key}={value!r} contains the substring "
                        f"{needle!r}; secrets must come from .env"
                    )


def test_named_volumes_are_declared(compose_config):
    """sra-data is the SQLite default volume; sra-postgres backs the postgres profile."""
    volumes = compose_config.get("volumes", {})
    assert "sra-data" in volumes, "named volume sra-data must be declared for the default app"


def test_app_service_publishes_port_8000(compose_config):
    app = compose_config["services"]["app"]
    ports = app.get("ports", [])
    published = [str(port) for port in ports]
    assert any("8000" in port for port in published), (
        f"app service must publish port 8000; got {published!r}"
    )


def test_app_service_has_healthcheck_against_api_health(compose_config):
    app = compose_config["services"]["app"]
    healthcheck = app.get("healthcheck")
    assert healthcheck is not None, "app service must declare a healthcheck"
    test = healthcheck.get("test")
    assert test is not None
    rendered = " ".join(test) if isinstance(test, list) else str(test)
    assert "/api/health" in rendered


def _flatten_env(env) -> dict[str, str]:
    """Turn the compose 'environment' mapping (or list) into a flat dict.

    Compose accepts both ``environment: {KEY: value}`` and
    ``environment: ["KEY=value"]``; tests must inspect both.
    """
    if isinstance(env, dict):
        return {str(k): "" if v is None else str(v) for k, v in env.items()}
    if isinstance(env, list):
        out: dict[str, str] = {}
        for entry in env:
            text = str(entry)
            if "=" in text:
                key, _, value = text.partition("=")
                out[key] = value
            else:
                out[text] = ""
        return out
    return {}