"""Tests for backend/scripts/patch_musicdl_py310.py (the Python 3.10 compat
shim for musicdl 2.13.x).

The script does not rewrite third-party source (import syntax has too many
legal shapes for a line-based rewriter to handle safely); it installs an
additive startup shim — ``koi_typing_compat.py`` + a ``.pth`` line — into the
active environment's site-packages that backports ``typing.Unpack`` from
``typing_extensions`` on Python < 3.11. These tests verify the install is
idempotent, that the shim makes ``from typing import Unpack`` work, and that
a module doing exactly what musicdl does (a fake client importing ``Unpack``
and using it in an annotation) loads — via the ``--site-packages`` test seam.
No real musicdl, no network, no mutation of the active environment.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "patch_musicdl_py310.py"

SHIM_FILE = "koi_typing_compat.py"
PTH_FILE = "koi_typing_compat.pth"

FAKE_CLIENT = """\
from typing import Unpack


class Kwargs:
    pass


class FakeClient:
    def __init__(self, **kwargs: Unpack[Kwargs]):
        pass
"""


def _run(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        text=True,
    )


def _py(code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    return tmp_path / "site"


@pytest.fixture
def fake_client_dir(tmp_path: Path) -> Path:
    client = tmp_path / "fake_musicdl"
    client.mkdir()
    (client / "__init__.py").write_text("", encoding="utf-8")
    (client / "client.py").write_text(FAKE_CLIENT, encoding="utf-8")
    return client


def _env_with(site_dir: Path, *extra_dirs: Path) -> dict:
    parts = [str(site_dir), *(str(d) for d in extra_dirs)]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(parts) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return env


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="shim only needed on Python < 3.11")
class TestPatchMusicdlPy310:
    def test_installs_shim_idempotently(self, site_dir):
        first = _run("--site-packages", str(site_dir))
        assert first.returncode == 0, first.stderr
        assert (site_dir / SHIM_FILE).is_file()
        assert (site_dir / PTH_FILE).read_text() == "import koi_typing_compat\n"
        assert "typing.Unpack = typing_extensions.Unpack" in (
            site_dir / SHIM_FILE
        ).read_text()

        second = _run("--site-packages", str(site_dir))
        assert second.returncode == 0, second.stderr
        assert "present koi_typing_compat.py present koi_typing_compat.pth" in second.stdout

    def test_shim_makes_typing_unpack_importable(self, site_dir):
        _run("--site-packages", str(site_dir))
        probe = _py(
            "import koi_typing_compat; import typing; "
            "from typing import Unpack; assert Unpack is typing.Unpack; "
            "print('unpack ok')",
            _env_with(site_dir),
        )
        assert probe.returncode == 0, probe.stderr
        assert "unpack ok" in probe.stdout

    def test_fake_client_imports_with_shim_active(self, site_dir, fake_client_dir):
        # A module doing exactly what musicdl does — `from typing import
        # Unpack` used in an annotation (evaluated at def time) — must load
        # once the shim has run.
        _run("--site-packages", str(site_dir))
        probe = _py(
            "import koi_typing_compat; "
            "from fake_musicdl.client import FakeClient; print('client ok')",
            _env_with(site_dir, fake_client_dir.parent),
        )
        assert probe.returncode == 0, probe.stderr
        assert "client ok" in probe.stdout
