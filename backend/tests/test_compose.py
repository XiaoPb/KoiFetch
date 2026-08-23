"""Tests for the Docker Compose stack wiring.

Docker is not installed in this environment, so instead of ``docker compose
config`` we parse ``docker-compose.yml`` with PyYAML and assert the contract
the release gate relies on: three services, ``env_file: .env``, bind mounts for
SQLite/Bubble/Pond storage, healthchecks, and explicit migration/worker startup
commands. The test skips when PyYAML is not installed (it is not a runtime
dependency). Full ``docker compose config`` validation is deferred to a
Docker-enabled environment.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

EXPECTED_SERVICES = {"backend", "worker", "frontend"}
BACKEND_MOUNTS = [
    "./data/db:/app/data/db",
    "./data/bubble:/app/data/bubble",
    "./data/pond:/app/data/pond",
]


@pytest.fixture(scope="module")
def compose() -> dict:
    with COMPOSE_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestStack:
    def test_compose_file_exists(self):
        assert COMPOSE_FILE.is_file()

    def test_three_services_present(self, compose):
        assert set(compose["services"]) == EXPECTED_SERVICES

    def test_stack_named(self, compose):
        assert compose.get("name") == "koi-fetch"


class TestBackendService:
    def test_env_file_is_dotenv(self, compose):
        assert compose["services"]["backend"]["env_file"] == ".env"

    def test_explicit_migration_then_server_command(self, compose):
        command = " ".join(compose["services"]["backend"]["command"])
        assert "alembic upgrade head" in command
        assert "uvicorn app.main:app" in command
        assert "--port 8000" in command

    def test_healthcheck_hits_health_endpoint(self, compose):
        test = " ".join(compose["services"]["backend"]["healthcheck"]["test"])
        assert "http://127.0.0.1:8000/api/health" in test

    def test_port_8000_exposed(self, compose):
        assert "8000:8000" in compose["services"]["backend"].get("ports", [])

    def test_bind_mounts_cover_db_bubble_pond(self, compose):
        volumes = compose["services"]["backend"]["volumes"]
        for mount in BACKEND_MOUNTS:
            assert mount in volumes

    def test_timezone_defaults_to_asia_shanghai(self, compose):
        environment = compose["services"]["backend"]["environment"]
        assert environment["TZ"] == "${TZ:-Asia/Shanghai}"


class TestWorkerService:
    def test_worker_command_is_entrypoint_module(self, compose):
        command = " ".join(compose["services"]["worker"]["command"])
        assert command == "python -m app.workers.main"

    def test_worker_shares_backend_image(self, compose):
        assert compose["services"]["worker"]["build"] == {"context": "./backend"}

    def test_worker_waits_for_backend_health(self, compose):
        depends = compose["services"]["worker"]["depends_on"]["backend"]
        assert depends["condition"] == "service_healthy"


class TestFrontendService:
    def test_frontend_port_maps_to_nginx(self, compose):
        assert "5173:80" in compose["services"]["frontend"].get("ports", [])

    def test_frontend_waits_for_backend_health(self, compose):
        depends = compose["services"]["frontend"]["depends_on"]["backend"]
        assert depends["condition"] == "service_healthy"
