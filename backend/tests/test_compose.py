"""Tests for the Docker Compose stack wiring.

Docker is not installed in this environment, so instead of ``docker compose
config`` we parse ``docker-compose.yml`` with PyYAML and assert the contract
the release gate relies on: three services, ``env_file: .env``, bind mounts for
SQLite/Bubble/Pond storage, healthchecks, and explicit migration/worker startup
commands. The test skips when PyYAML is not installed (it is not a runtime
dependency). Full ``docker compose config`` validation is deferred to a
Docker-enabled environment.

This module also statically validates the Task 17 *smoke contract* for the
compose variant (authored but not executed here — Docker is absent): the
backend command chains ``alembic upgrade head && seed && uvicorn``, the worker
runs ``python -m app.workers.main``, the backend healthcheck runs the readiness
probe (``python -m app.health``, which requires ``code == 0`` from
``/api/health``), and the frontend Nginx proxies both ``/api`` and ``/ws`` to
the backend — the exact routing the end-to-end smoke walks over HTTP. The
executable offline smoke itself lives in ``test_smoke.py``.
"""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"

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


class TestNginxProxy:
    """The smoke contract's frontend hop: Nginx routes /api + /ws to backend.

    The offline smoke (test_smoke.py) walks the API flow through the backend
    directly; under Docker the browser talks to the frontend Nginx, which must
    proxy REST and WebSocket traffic to the ``backend`` service on port 8000.
    These assertions pin that routing statically (the compose variant is
    authored + validated but not executed in this Docker-less environment).
    """

    def test_nginx_conf_exists(self):
        assert NGINX_CONF.is_file()

    def test_api_location_proxies_to_backend(self):
        conf = NGINX_CONF.read_text(encoding="utf-8")
        block = self._nginx_block(conf, "/api/")
        assert "proxy_pass http://backend:8000;" in block
        # The block must end at its own indented closing brace — a capture
        # that swallows the following /ws block would let a missing /api
        # proxy_pass pass as long as /ws still has one.
        assert "location /ws" not in block

    def test_ws_location_proxies_to_backend_with_upgrade(self):
        conf = NGINX_CONF.read_text(encoding="utf-8")
        block = self._nginx_block(conf, "/ws")
        assert "proxy_pass http://backend:8000;" in block
        assert "proxy_http_version 1.1;" in block
        assert "proxy_set_header Upgrade $http_upgrade;" in block
        assert 'proxy_set_header Connection "upgrade";' in block

    @staticmethod
    def _nginx_block(conf: str, location: str) -> str:
        """The body of an nginx ``location <location> { ... }`` block.

        Regex-based so a renamed/missing location fails with a clear assertion
        message instead of an obscure IndexError from naive string splitting.
        The block terminates at its own *indented* closing brace
        (``^\s*\}`` under MULTILINE): nginx indents every block's ``}``, so a
        bare ``\n\}`` terminator would over-capture through to the first
        column-0 brace and swallow following blocks.
        """
        match = re.search(
            rf"location {re.escape(location)} \{{(.*?)^\s*\}}",
            conf,
            re.DOTALL | re.MULTILINE,
        )
        assert match is not None, (
            f"nginx.conf is missing the 'location {location}' block"
        )
        return match.group(1)
