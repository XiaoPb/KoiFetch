"""Shared pytest fixtures for the backend test suite."""

import os

import pytest

# Importing ``app.main`` constructs the module-level FastAPI instance, which
# calls ``get_settings()`` and therefore requires the two secrets. Provide safe
# local values for the whole test process (this module is imported before any
# test module). Tests that care about a hermetic environment delete these via
# ``clean_env`` in test_config.py.
os.environ.setdefault("ADMIN_PASSWORD", "pw")
os.environ.setdefault("SECRET_KEY", "sk")


@pytest.fixture(autouse=True)
def no_dotenv_file(monkeypatch):
    """Keep all tests hermetic against a real repo-root ``.env`` file.

    ``Settings.from_env()``/``get_settings()`` load ``.env`` by default for the
    documented local-dev workflow, so a developer's real ``.env`` would leak
    into tests that assert on defaults or missing secrets. Every test module in
    this package therefore treats ``load_dotenv`` as a no-op, unless a
    test class overrides this fixture (same name) to exercise real ``.env``
    loading — see ``TestDotenvLoading`` in test_config.py.
    """
    import app.infrastructure.config as config

    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)
