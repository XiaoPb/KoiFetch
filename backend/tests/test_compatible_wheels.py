"""Contracts for the reproducible third-party compatibility wheel builder."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "backend" / "scripts" / "build_compatible_wheels.py"
INSTALLER = REPO_ROOT / "backend" / "scripts" / "install_backend_dependencies.py"
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"


def _load_builder():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_compatible_wheels", BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _wheel(path: Path, metadata: str) -> None:
    dist_info = "sample-1.0.0.dist-info"
    files = {
        "sample/__init__.py": b"VALUE = 1\n",
        f"{dist_info}/METADATA": metadata.encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr(f"{dist_info}/RECORD", "")


def _record_entries(path: Path) -> dict[str, tuple[str, str]]:
    with zipfile.ZipFile(path) as archive:
        record_name = next(name for name in archive.namelist() if name.endswith("/RECORD"))
        rows = csv.reader(io.TextIOWrapper(archive.open(record_name), encoding="utf-8"))
        return {row[0]: (row[1], row[2]) for row in rows}


def test_rewrite_wheel_updates_metadata_and_rebuilds_record(tmp_path):
    builder = _load_builder()
    source = tmp_path / "sample-1.0.0-py3-none-any.whl"
    _wheel(
        source,
        "Metadata-Version: 2.1\n"
        "Name: sample\n"
        "Version: 1.0.0\n"
        "Requires-Dist: olddep==1\n",
    )
    output = builder.rewrite_wheel(
        source,
        tmp_path / "out",
        remove_dependencies=True,
    )

    with zipfile.ZipFile(output) as archive:
        metadata = archive.read("sample-1.0.0.dist-info/METADATA").decode()
        assert "Requires-Dist:" not in metadata
    records = _record_entries(output)
    with zipfile.ZipFile(output) as archive:
        for name, (digest, size) in records.items():
            if not digest:
                assert name.endswith("/RECORD")
                continue
            content = archive.read(name)
            encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
            assert digest == f"sha256={encoded.decode()}"
            assert size == str(len(content))


def test_pinned_upstream_wheels_and_security_rewrite_contracts():
    builder = _load_builder()
    assert builder.WHEEL_SPECS["musicdl"].version == "2.13.6"
    assert builder.WHEEL_SPECS["f2"].version == "0.0.1.7"
    for spec in builder.WHEEL_SPECS.values():
        assert spec.url.startswith("https://files.pythonhosted.org/")
        assert len(spec.sha256) == 64
        assert spec.sha256 == spec.sha256.lower()
    assert builder.MUSICDL_CRYPTOGRAPHY_REQUIREMENT == "cryptography<51,>=50.0.1"


def test_metadata_diff_is_narrow_and_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    builder = _load_builder()
    original = (
        b"Metadata-Version: 2.1\nName: musicdl\n"
        b"Requires-Dist: cryptography<47,>=46.0.5\n"
        b"Requires-Dist: requests\n"
    )
    rewritten = builder._rewrite_metadata("musicdl", original).decode()
    assert "Requires-Dist: cryptography<51,>=50.0.1" in rewritten
    assert "Requires-Dist: requests" in rewritten
    assert "cryptography<47" not in rewritten

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(b"tampered"))
    spec = builder.WheelSpec("x", "1", "https://example.invalid/x.whl", "0" * 64)
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        builder._download(spec, tmp_path)


def test_install_entrypoints_build_compat_wheels_before_pip_audit_and_checks():
    installer = INSTALLER.read_text(encoding="utf-8")
    docker = DOCKERFILE.read_text(encoding="utf-8")
    requirements = REQUIREMENTS.read_text(encoding="utf-8")
    assert "build_compatible_wheels" in installer
    assert "musicdl" in installer and "f2" in installer
    assert "force-reinstall" in installer
    assert "pip==26.2.1" in installer
    assert "pip check" in installer
    assert "import f2.exceptions" in installer and "import musicdl" in installer
    assert "cryptography>=50.0.1,<51" in requirements
    assert "python backend/scripts/install_backend_dependencies.py" in docker
    assert "pip install --no-cache-dir -r requirements.txt" not in docker
    assert "pip_audit" not in installer
