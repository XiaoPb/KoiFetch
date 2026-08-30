"""Contracts for the reproducible third-party compatibility wheel builder."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "backend" / "scripts" / "build_compatible_wheels.py"
INSTALLER = REPO_ROOT / "backend" / "scripts" / "install_backend_dependencies.py"
AUDIT_SCRIPT = REPO_ROOT / "backend" / "scripts" / "audit_backend_dependencies.py"
CONSTRAINTS = REPO_ROOT / "backend" / "constraints.txt"
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"
DEPLOY_NATIVE = REPO_ROOT / "deploy-native.sh"


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


def _named_wheel(path: Path, name: str, version: str, metadata: str) -> None:
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": b"VALUE = 1\n",
        f"{dist_info}/METADATA": metadata.encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
        f"{dist_info}/RECORD": b"",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry, value in files.items():
            archive.writestr(entry, value)


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
        "Requires-Dist: pytest==8.3.4\n",
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


@pytest.mark.parametrize(
    "entry_name",
    ["/absolute.txt", "..\\outside.txt", "../outside.txt", "safe/../outside.txt"],
)
def test_rewrite_wheel_rejects_unsafe_zip_paths(tmp_path, entry_name):
    builder = _load_builder()
    source = tmp_path / "sample-1.0.0-py3-none-any.whl"
    _wheel(source, "Metadata-Version: 2.1\nName: sample\nVersion: 1.0.0\n")
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr(entry_name, b"unsafe")
    with pytest.raises(RuntimeError, match="unsafe wheel entry path"):
        builder.rewrite_wheel(source, tmp_path / "out")


def test_rewrite_wheel_rejects_duplicate_entries_and_ambiguous_dist_info(tmp_path):
    builder = _load_builder()
    duplicate = tmp_path / "duplicate-1.0.0-py3-none-any.whl"
    _wheel(duplicate, "Metadata-Version: 2.1\nName: sample\nVersion: 1.0.0\n")
    with zipfile.ZipFile(duplicate, "a") as archive:
        archive.writestr("sample-1.0.0.dist-info/METADATA", b"duplicate")
    with pytest.raises(RuntimeError, match="duplicate wheel entry"):
        builder.rewrite_wheel(duplicate, tmp_path / "out-duplicate")

    ambiguous = tmp_path / "ambiguous-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(ambiguous, "w") as archive:
        for dist_info in ("sample-1.0.0.dist-info", "other-1.0.0.dist-info"):
            archive.writestr(f"{dist_info}/METADATA", b"Metadata-Version: 2.1\n")
            archive.writestr(f"{dist_info}/RECORD", b"")
    with pytest.raises(RuntimeError, match="exactly one dist-info"):
        builder.rewrite_wheel(ambiguous, tmp_path / "out-ambiguous")


def test_rewrite_wheel_validates_expected_filename_and_metadata_identity(tmp_path):
    builder = _load_builder()
    source = tmp_path / "f2-0.0.1.7-py3-none-any.whl"
    _named_wheel(
        source,
        "f2",
        "0.0.1.7",
        "Metadata-Version: 2.4\nName: f2\nVersion: 0.0.1.7\n",
    )
    expected = builder.WheelSpec("f2", "0.0.1.7", "https://example.invalid/f2.whl", "0" * 64)
    output = builder.rewrite_wheel(source, tmp_path / "out", expected_spec=expected)
    assert output.is_file()

    wrong_name = tmp_path / "f2-0.0.1.7-py3-none-any.whl"
    _named_wheel(
        wrong_name,
        "other",
        "0.0.1.7",
        "Metadata-Version: 2.4\nName: other\nVersion: 0.0.1.7\n",
    )
    with pytest.raises(RuntimeError, match="wheel metadata Name"):
        builder.rewrite_wheel(wrong_name, tmp_path / "out-wrong", expected_spec=expected)


def test_metadata_rewrite_uses_exact_pep508_names_and_rejects_unsupported_shapes():
    builder = _load_builder()
    with pytest.raises(RuntimeError, match="unreviewed f2 runtime dependency: cryptography-extra"):
        builder._rewrite_metadata(
            "f2",
            b"Metadata-Version: 2.4\nName: f2\nVersion: 0.0.1.7\n"
            b"Requires-Dist: cryptography-extra==1\n",
        )
    with pytest.raises(RuntimeError, match="extras or markers"):
        builder._rewrite_metadata(
            "f2",
            b"Metadata-Version: 2.4\nName: f2\nVersion: 0.0.1.7\n"
            b"Requires-Dist: cryptography[foo]==44.0.0\n",
        )


def test_pinned_upstream_wheels_and_security_rewrite_contracts():
    builder = _load_builder()
    assert builder.WHEEL_SPECS["musicdl"].version == "2.13.6"
    assert builder.WHEEL_SPECS["f2"].version == "0.0.1.7"
    for spec in builder.WHEEL_SPECS.values():
        assert spec.url.startswith("https://files.pythonhosted.org/")
        assert len(spec.sha256) == 64
        assert spec.sha256 == spec.sha256.lower()
    assert builder.MUSICDL_CRYPTOGRAPHY_REQUIREMENT == "cryptography<51,>=50.0.1"


def test_f2_compatibility_metadata_covers_every_upstream_runtime_requirement():
    builder = _load_builder()
    upstream_runtime = {
        "aiofiles",
        "aiosqlite",
        "browser-cookie3",
        "click",
        "cryptography",
        "gmssl",
        "httpx",
        "importlib-resources",
        "jsonpath-ng",
        "m3u8",
        "protobuf",
        "pydantic",
        "pyexecjs",
        "pyyaml",
        "qrcode",
        "rich",
        "websockets-proxy",
        "websockets",
    }
    assert set(builder._F2_COMPAT_REQUIREMENTS) == upstream_runtime


def test_f2_rewrite_preserves_each_reviewed_runtime_requirement_and_drops_only_dev_deps():
    builder = _load_builder()
    upstream = (
        "Metadata-Version: 2.4\n"
        "Name: f2\n"
        "Version: 0.0.1.7\n"
        "Requires-Dist: aiofiles==24.1.0\n"
        "Requires-Dist: aiosqlite==0.20.0\n"
        "Requires-Dist: babel==2.13.0\n"
        "Requires-Dist: black==24.10.0\n"
        "Requires-Dist: browser-cookie3==0.20.1\n"
        "Requires-Dist: click==8.1.7\n"
        "Requires-Dist: cryptography==44.0.0\n"
        "Requires-Dist: gmssl==3.2.2\n"
        "Requires-Dist: httpx==0.27.2\n"
        "Requires-Dist: importlib-resources==6.4.5\n"
        "Requires-Dist: jsonpath-ng==1.6.1\n"
        "Requires-Dist: m3u8==3.6.0\n"
        "Requires-Dist: protobuf==5.28.3\n"
        "Requires-Dist: pydantic==2.9.*\n"
        "Requires-Dist: pyexecjs==1.5.1\n"
        "Requires-Dist: pytest-asyncio==0.25.0\n"
        "Requires-Dist: pytest==8.3.4\n"
        "Requires-Dist: pyyaml==6.0.2\n"
        "Requires-Dist: qrcode==8.0\n"
        "Requires-Dist: rich==13.9.3\n"
        "Requires-Dist: websockets-proxy==0.1.2\n"
        "Requires-Dist: websockets<13.0\n"
    )
    rewritten = builder._rewrite_metadata("f2", upstream.encode()).decode()
    requires = [
        line.removeprefix("Requires-Dist: ")
        for line in rewritten.splitlines()
        if line.startswith("Requires-Dist:")
    ]
    assert requires == list(builder._F2_COMPAT_REQUIREMENTS.values())
    assert "babel" not in rewritten
    assert "black" not in rewritten
    assert "pytest" not in rewritten


def test_f2_rewrite_fails_closed_for_unreviewed_runtime_dependency():
    builder = _load_builder()
    with pytest.raises(RuntimeError, match="unreviewed f2 runtime dependency: requests"):
        builder._rewrite_metadata(
            "f2",
            b"Metadata-Version: 2.4\nName: f2\nVersion: 0.0.1.7\n"
            b"Requires-Dist: requests==2.32.0\n",
        )


def test_metadata_diff_is_narrow_and_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    builder = _load_builder()
    original = (
        b"Metadata-Version: 2.1\nName: musicdl\nVersion: 2.13.6\n"
        b"Requires-Dist: cryptography<47,>=46.0.5\n"
        b"Requires-Dist: requests\n"
    )
    rewritten = builder._rewrite_metadata("musicdl", original).decode()
    assert "Requires-Dist: cryptography<51,>=50.0.1" in rewritten
    assert "Requires-Dist: requests" in rewritten
    assert "cryptography<47" not in rewritten
    f2_metadata = builder._rewrite_metadata(
        "f2",
        b"Metadata-Version: 2.1\nName: f2\nVersion: 0.0.1.7\n"
        b"Requires-Dist: aiofiles==24.1.0\n"
        b"Requires-Dist: pytest==8.3.4\n"
        b"Requires-Dist: cryptography==44.0.0\n",
    ).decode()
    assert "Requires-Dist: aiofiles>=24.1.0" in f2_metadata
    assert "Requires-Dist: cryptography<51,>=50.0.1" in f2_metadata
    assert "pytest==8.3.4" not in f2_metadata

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
    assert "patch_musicdl_py310.py" in installer
    assert "--no-deps" not in installer
    assert "pip check" in installer
    assert "constraints.txt" in installer
    assert "import f2.exceptions" in installer and "import musicdl" in installer
    assert "cryptography>=50.0.1,<51" in requirements
    assert "python backend/scripts/install_backend_dependencies.py" in docker
    assert "backend/constraints.txt" in docker
    assert "--skip-smoke" in docker
    assert "pip install --no-cache-dir -r requirements.txt" not in docker
    assert "pip_audit" not in installer


def test_docker_image_contains_cookie_migration_script():
    docker = DOCKERFILE.read_text(encoding="utf-8")
    assert "backend/scripts/encrypt_platform_cookies.py" in docker
    assert docker.index("COPY backend/requirements.txt") < docker.index("COPY backend/app")
    assert docker.index("COPY backend/app") < docker.index("from musicdl import musicdl")


def test_backend_constraints_lock_the_runtime_and_reviewed_wheels():
    constraints = CONSTRAINTS.read_text(encoding="utf-8")
    assert "packaging==26.3" in constraints
    assert "f2==0.0.1.7" in constraints
    assert "musicdl==2.13.6" in constraints
    assert "parse-video-py" in constraints and "exact commit" in constraints


def test_native_deploy_uses_only_the_unified_dependency_installer():
    deploy = DEPLOY_NATIVE.read_text(encoding="utf-8")
    assert "install_backend_dependencies.py" in deploy
    assert '--cache "$PIP_CACHE"' in deploy
    assert 'pip install -r "$ROOT/backend/requirements.txt"' not in deploy
    assert "patch_musicdl_py310.py" not in deploy
    assert "install_f2.sh" not in deploy
    assert "npm ci --prefix frontend --cache \"$NPM_CACHE\"" in deploy
    assert "[ ! -d \"$ROOT/frontend/node_modules\" ]" not in deploy


@pytest.mark.integration
@pytest.mark.live_install
def test_live_clean_venv_installs_and_smoke_tests_real_application(monkeypatch, tmp_path):
    """Opt-in network contract for the exact clean-environment install path."""
    if os.environ.get("KOIFETCH_RUN_LIVE_INSTALL") != "1":
        pytest.skip("set KOIFETCH_RUN_LIVE_INSTALL=1 for the networked clean-venv gate")

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(REPO_ROOT / "backend") + (
        os.pathsep + environment["PYTHONPATH"]
        if environment.get("PYTHONPATH")
        else ""
    )
    subprocess.run(
        [str(python), str(INSTALLER), "--cache", str(tmp_path / "wheel-cache")],
        check=True,
        cwd=REPO_ROOT,
        env=environment,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True, env=environment)
    subprocess.run(
        [str(python), "-m", "pip", "install", "pip-audit==2.10.1"],
        check=True,
        cwd=REPO_ROOT,
        env=environment,
    )
    subprocess.run(
        [str(python), str(AUDIT_SCRIPT)],
        check=True,
        cwd=REPO_ROOT,
        env=environment,
    )
    subprocess.run(
        [
            str(python),
            "-c",
            "from musicdl import musicdl; "
            "from app.adapters.safe_upstream import SafeUpstreamClient; "
            "print(musicdl.MusicClient.__name__, SafeUpstreamClient.__name__)",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=environment,
    )
