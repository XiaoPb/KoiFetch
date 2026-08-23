"""Shared pytest fixtures for the backend test suite."""

import pytest


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
