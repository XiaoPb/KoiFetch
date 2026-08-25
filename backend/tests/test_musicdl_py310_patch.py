"""Tests for backend/scripts/patch_musicdl_py310.py (the Python 3.10 compat
shim for musicdl 2.13.x).

Builds a fake musicdl package containing every import form the real package
uses (single-name ``from typing import Unpack``, multi-name
``from typing import Dict, Any, Unpack``, already-patched, and Unpack-free)
and runs the script against it via subprocess — no real musicdl, no network,
no mutation of the active environment. Regression coverage for the gdstudio.py
multi-name form that a naive string replacement silently skipped.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "patch_musicdl_py310.py"


def _write(package_dir: Path, name: str, content: str) -> None:
    (package_dir / name).write_text(content, encoding="utf-8")


def _run(package_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--package-dir", str(package_dir)],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def fake_package(tmp_path: Path) -> Path:
    pkg = tmp_path / "musicdl"
    pkg.mkdir()
    _write(pkg, "__init__.py", "")
    # Single-name form (23 files in the real package).
    _write(pkg, "single.py", "from typing import Unpack\nx: Unpack[Kwargs]\n")
    # Multi-name form (gdstudio.py in the real package).
    _write(pkg, "multi.py", "from typing import Dict, Any, Unpack\ny: Unpack[Kwargs]\n")
    # Already-patched form (idempotency).
    _write(pkg, "patched.py", "from typing_extensions import Unpack\nz: Unpack[Kwargs]\n")
    # No Unpack at all — must be untouched.
    _write(pkg, "clean.py", "from typing import Dict\nw: Dict = {}\n")
    return pkg


class TestPatchMusicdlPy310:
    def test_rewrites_all_forms_and_passes_post_condition(self, fake_package):
        result = _run(fake_package)
        assert result.returncode == 0, result.stderr
        assert "post-condition ok" in result.stdout
        assert (fake_package / "single.py").read_text() == (
            "from typing_extensions import Unpack\nx: Unpack[Kwargs]\n"
        )
        assert (fake_package / "multi.py").read_text() == (
            "from typing import Dict, Any\n"
            "from typing_extensions import Unpack\n"
            "y: Unpack[Kwargs]\n"
        )
        assert (fake_package / "patched.py").read_text() == (
            "from typing_extensions import Unpack\nz: Unpack[Kwargs]\n"
        )
        assert (fake_package / "clean.py").read_text() == (
            "from typing import Dict\nw: Dict = {}\n"
        )

    def test_idempotent_second_run(self, fake_package):
        first = _run(fake_package)
        second = _run(fake_package)
        assert first.returncode == 0 and second.returncode == 0
        assert "patched 2 file(s), 1 already patched" in first.stdout
        assert "patched 0 file(s), 3 already patched" in second.stdout

    def test_missing_package_dir_fails(self, tmp_path):
        result = _run(tmp_path / "nope")
        assert result.returncode == 1
        assert "not a directory" in result.stderr

    def test_crlf_line_endings_preserved(self, tmp_path):
        pkg = tmp_path / "musicdl"
        pkg.mkdir()
        _write(pkg, "__init__.py", "")
        _write(pkg, "crlf.py", "from typing import Unpack\r\nx: Unpack[Kwargs]\r\n")
        result = _run(pkg)
        assert result.returncode == 0
        assert (pkg / "crlf.py").read_bytes() == (
            b"from typing_extensions import Unpack\r\nx: Unpack[Kwargs]\r\n"
        )
