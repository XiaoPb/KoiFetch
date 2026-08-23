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

    def test_all_services_restart_unless_stopped(self, compose):
        for name in sorted(EXPECTED_SERVICES):
            assert compose["services"][name].get("restart") == "unless-stopped"


class TestBackendService:
    def test_env_file_is_dotenv(self, compose):
        assert compose["services"]["backend"]["env_file"] == ".env"

    def test_explicit_migration_then_server_command(self, compose):
        command = " ".join(compose["services"]["backend"]["command"])
        assert "alembic upgrade head" in command
        assert "uvicorn app.main:app" in command
        assert "--port 8000" in command

    def test_healthcheck_runs_readiness_probe(self, compose):
        # The healthcheck must reflect storage readiness, not plain HTTP 200:
        # /api/health always returns 200 and reports readiness in the body, so
        # the healthcheck runs the app's probe (app/health.py), which requires
        # code == 0. That is what makes `depends_on: service_healthy` on
        # worker/frontend gate on full service AND storage readiness.
        test = " ".join(compose["services"]["backend"]["healthcheck"]["test"])
        assert test == "CMD python -m app.health"

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

    def test_frontend_has_healthcheck(self, compose):
        # Nginx serves /usr/share/nginx/html; busybox wget ships in nginx:alpine.
        test = " ".join(compose["services"]["frontend"]["healthcheck"]["test"])
        assert "wget -q -O /dev/null http://127.0.0.1/" in test
