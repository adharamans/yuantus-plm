from __future__ import annotations

from pathlib import Path

import yaml


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "pyproject.toml").is_file() and (cur / "docker-compose.yml").is_file():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise AssertionError(
        "Could not locate repo root (expected pyproject.toml + docker-compose.yml)"
    )


def _env_to_dict(env):
    if env is None:
        return {}
    if isinstance(env, dict):
        return env
    if isinstance(env, list):
        out = {}
        for item in env:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                out[k] = v
        return out
    raise AssertionError(f"Unexpected environment type: {type(env)}")


def _as_str_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, str):
        return [val]
    raise AssertionError(f"Unexpected list-like type: {type(val)}")


def _load_compose_services() -> dict:
    repo_root = _find_repo_root(Path(__file__))
    compose_yml = repo_root / "docker-compose.yml"
    assert compose_yml.is_file(), f"Missing {compose_yml}"

    doc = yaml.safe_load(compose_yml.read_text(encoding="utf-8", errors="replace"))
    return (doc or {}).get("services") or {}


def test_compose_worker_sets_dedup_vision_base_url() -> None:
    services = _load_compose_services()
    worker = services.get("worker") or {}
    env = _env_to_dict(worker.get("environment"))

    assert "YUANTUS_DEDUP_VISION_BASE_URL" in env, (
        "docker-compose.yml worker service must set YUANTUS_DEDUP_VISION_BASE_URL so "
        "cad_dedup_vision jobs can reach the Dedup Vision service from inside the container."
    )
    val = env.get("YUANTUS_DEDUP_VISION_BASE_URL")
    assert isinstance(val, str) and val.strip(), (
        "docker-compose.yml worker YUANTUS_DEDUP_VISION_BASE_URL must be a non-empty string "
        f"(got: {val!r})"
    )
    assert "dedup-vision:8000" in val, (
        "docker-compose.yml worker YUANTUS_DEDUP_VISION_BASE_URL must target the compose "
        "service hostname (expected to include 'dedup-vision:8000')."
        f"\nGot: {val}"
    )

    fallback_port_val = env.get("YUANTUS_DEDUP_VISION_FALLBACK_PORT")
    assert isinstance(fallback_port_val, str) and fallback_port_val.strip(), (
        "docker-compose.yml worker should set YUANTUS_DEDUP_VISION_FALLBACK_PORT so "
        "host-network fallback can follow non-default Dedup host port mappings."
        f"\nGot: {fallback_port_val!r}"
    )
    assert "DEDUP_VISION_PORT" in fallback_port_val or "8100" in fallback_port_val, (
        "worker fallback port should derive from DEDUP_VISION_PORT with default 8100."
        f"\nGot: {fallback_port_val}"
    )

    fallback_base_url_val = env.get("YUANTUS_DEDUP_VISION_FALLBACK_BASE_URL")
    assert isinstance(fallback_base_url_val, str), (
        "docker-compose.yml worker should expose YUANTUS_DEDUP_VISION_FALLBACK_BASE_URL "
        "for explicit fallback endpoint overrides."
        f"\nGot: {fallback_base_url_val!r}"
    )


def test_compose_api_sets_dedup_fallback_env_vars() -> None:
    services = _load_compose_services()
    api = services.get("api") or {}
    env = _env_to_dict(api.get("environment"))

    fallback_port_val = env.get("YUANTUS_DEDUP_VISION_FALLBACK_PORT")
    assert isinstance(fallback_port_val, str) and fallback_port_val.strip(), (
        "docker-compose.yml api should set YUANTUS_DEDUP_VISION_FALLBACK_PORT "
        "to keep API-side Dedup fallback behavior aligned with worker."
        f"\nGot: {fallback_port_val!r}"
    )
    assert "DEDUP_VISION_PORT" in fallback_port_val or "8100" in fallback_port_val, (
        "api fallback port should derive from DEDUP_VISION_PORT with default 8100."
        f"\nGot: {fallback_port_val}"
    )

    fallback_base_url_val = env.get("YUANTUS_DEDUP_VISION_FALLBACK_BASE_URL")
    assert isinstance(fallback_base_url_val, str), (
        "docker-compose.yml api should expose YUANTUS_DEDUP_VISION_FALLBACK_BASE_URL "
        "for explicit fallback endpoint overrides."
        f"\nGot: {fallback_base_url_val!r}"
    )


def test_compose_worker_sets_host_gateway_mapping_for_dedup_fallback() -> None:
    services = _load_compose_services()
    worker = services.get("worker") or {}
    extra_hosts = _as_str_list(worker.get("extra_hosts"))

    assert extra_hosts, (
        "docker-compose.yml worker must define extra_hosts so host.docker.internal is resolvable "
        "for Dedup Vision host-network fallback."
    )
    assert "host.docker.internal:host-gateway" in extra_hosts, (
        "docker-compose.yml worker extra_hosts must include "
        "'host.docker.internal:host-gateway' for Linux compatibility."
        f"\nGot: {extra_hosts}"
    )


def test_compose_api_and_worker_expose_render_service_env() -> None:
    services = _load_compose_services()
    required = {
        "YUANTUS_RENDER_SERVICE_BASE_URL",
        "YUANTUS_RENDER_SERVICE_SERVICE_TOKEN",
        "YUANTUS_RENDER_SERVICE_TIMEOUT_SECONDS",
        "YUANTUS_CIRCUIT_BREAKER_RENDER_SERVICE_ENABLED",
        "YUANTUS_CIRCUIT_BREAKER_RENDER_SERVICE_FAILURE_THRESHOLD",
        "YUANTUS_CIRCUIT_BREAKER_RENDER_SERVICE_WINDOW_SECONDS",
        "YUANTUS_CIRCUIT_BREAKER_RENDER_SERVICE_RECOVERY_SECONDS",
        "YUANTUS_CIRCUIT_BREAKER_RENDER_SERVICE_HALF_OPEN_MAX_CALLS",
        "YUANTUS_CIRCUIT_BREAKER_RENDER_SERVICE_BACKOFF_MAX_SECONDS",
    }

    for service_name in ("api", "worker"):
        service = services.get(service_name) or {}
        env = _env_to_dict(service.get("environment"))
        missing = sorted(required - set(env))
        assert not missing, (
            f"docker-compose.yml {service_name} must expose render-service env vars "
            f"so DXF preview jobs can enable the VemCAD render service. Missing: {missing}"
        )
        assert env["YUANTUS_RENDER_SERVICE_BASE_URL"] == "${YUANTUS_RENDER_SERVICE_BASE_URL:-}"
        assert env["YUANTUS_CIRCUIT_BREAKER_RENDER_SERVICE_ENABLED"].endswith(":-false}")
