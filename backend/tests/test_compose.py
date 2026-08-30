"""Tests for the Docker Compose stack wiring.

Docker is not installed in this environment, so instead of ``docker compose
config`` we parse ``docker-compose.yml`` with PyYAML and assert the contract
the release gate relies on: two services, ``env_file: .env``, bind mounts for
SQLite/Bubble/Pond storage, healthchecks, and explicit migration/worker startup
commands. The test skips when PyYAML is not installed (it is not a runtime
dependency). Full ``docker compose config`` validation is deferred to a
Docker-enabled environment.

This module also statically validates the Task 17/18 *smoke contract* for the
compose variant (authored but not executed here — Docker is absent): the
backend command chains ``alembic upgrade head && seed && uvicorn``, the worker
runs ``python -m app.workers.main``, and the backend healthcheck runs the
readiness probe (``python -m app.health``, which requires ``code == 0`` from
``/api/health``). There is no frontend service and no Nginx anymore: the
backend image builds the frontend and serves it at "/", so ``/api`` and
``/ws`` are same-origin — the exact routing the end-to-end smoke walks over
HTTP. The executable offline smoke itself lives in ``test_smoke.py`` and the
static-serving contract (SPA fallback, API-404 preservation) in
``test_static.py``.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"

EXPECTED_SERVICES = {"backend", "worker"}
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

    def test_two_services_present(self, compose):
        assert set(compose["services"]) == EXPECTED_SERVICES

    def test_no_frontend_service(self, compose):
        # Nginx is gone: the backend serves the built frontend at "/", so the
        # stack is exactly backend (API + static) + worker.
        assert "frontend" not in compose["services"]

    def test_nginx_conf_removed(self):
        # The container-internal Nginx was deleted with the no-Nginx switch;
        # this guards against accidentally resurrecting it.
        assert not NGINX_CONF.exists()

    def test_stack_named(self, compose):
        assert compose.get("name") == "koi-fetch"

    def test_all_services_restart_unless_stopped(self, compose):
        for name in sorted(EXPECTED_SERVICES):
            assert compose["services"][name].get("restart") == "unless-stopped"


class TestBackendService:
    def test_env_file_is_dotenv(self, compose):
        assert compose["services"]["backend"]["env_file"] == ".env"

    def test_backend_uses_published_ghcr_image(self, compose):
        service = compose["services"]["backend"]
        assert service["image"] == "${KOIFETCH_IMAGE:-ghcr.io/xiaopb/koifetch:latest}"
        assert service["pull_policy"] == "always"

    def test_frontend_dist_path_points_into_image(self, compose):
        # The image copies the Vite build to /app/static; compose must point
        # FRONTEND_DIST_PATH there so the backend serves it at "/".
        environment = compose["services"]["backend"]["environment"]
        assert environment["FRONTEND_DIST_PATH"] == "/app/static"

    def test_explicit_migration_then_server_command(self, compose):
        command = " ".join(compose["services"]["backend"]["command"])
        assert "alembic upgrade head" in command
        assert "uvicorn app.main:app" in command
        assert "--port 8000" in command

    def test_admin_seed_runs_after_migrations(self, compose):
        # The backend command must chain the idempotent admin seed between
        # migrations and the API server (app/infrastructure/seed.py).
        command = " ".join(compose["services"]["backend"]["command"])
        assert (
            "alembic upgrade head && python -m app.infrastructure.seed && uvicorn app.main:app"
            in command
        )

    def test_healthcheck_runs_readiness_probe(self, compose):
        # The healthcheck must reflect storage readiness, not plain HTTP 200:
        # /api/health always returns 200 and reports readiness in the body, so
        # the healthcheck runs the app's probe (app/health.py), which requires
        # code == 0. That is what makes `depends_on: service_healthy` on the
        # worker gate on full service AND storage readiness.
        test = " ".join(compose["services"]["backend"]["healthcheck"]["test"])
        assert test == "CMD python -m app.health"

    def test_port_8000_exposed(self, compose):
        # One origin for the SPA and the API/WS: host port 8000 maps to the
        # container's uvicorn, which serves both.
        assert "8000:8000" in compose["services"]["backend"].get("ports", [])

    def test_bind_mounts_cover_db_bubble_pond(self, compose):
        volumes = compose["services"]["backend"]["volumes"]
        for mount in BACKEND_MOUNTS:
            assert mount in volumes

    def test_timezone_defaults_to_asia_shanghai(self, compose):
        environment = compose["services"]["backend"]["environment"]
        assert environment["TZ"] == "${TZ:-Asia/Shanghai}"

    def test_cookie_encryption_key_is_injected_without_literal_secret(self, compose):
        for service in (compose["services"]["backend"], compose["services"]["worker"]):
            value = service["environment"]["COOKIE_ENCRYPTION_KEY"]
            assert value.startswith("${COOKIE_ENCRYPTION_KEY:")
            assert "AAAAAAAA" not in value


class TestWorkerService:
    def test_worker_command_is_entrypoint_module(self, compose):
        command = " ".join(compose["services"]["worker"]["command"])
        assert command == "python -m app.workers.main"

    def test_worker_shares_backend_image(self, compose):
        worker = compose["services"]["worker"]
        assert worker["image"] == compose["services"]["backend"]["image"]
        assert worker["pull_policy"] == "always"

    def test_worker_waits_for_backend_health(self, compose):
        depends = compose["services"]["worker"]["depends_on"]["backend"]
        assert depends["condition"] == "service_healthy"
