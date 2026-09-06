"""Regression tests for the native deployment script."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy-native.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")


FAKE_PYTHON = r'''#!/usr/bin/env bash
set -eu

if [[ "$*" == *"install_backend_dependencies.py"* ]]; then
  printf '1\n' > "$TEST_ROOT/installer.count"
  exit 0
fi

case " $* " in
  *" -m uvicorn "*) while true; do sleep 1; done ;;
  *" -m app.workers.main "*) while true; do sleep 1; done ;;
esac
'''

FAKE_CURL = r'''#!/usr/bin/env bash
printf '%s\n' '{"code":0,"data":{"status":"ok"}}'
'''


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def write_fake_root(tmp_path: Path, env_text: str = "") -> None:
    script = tmp_path / "deploy-native.sh"
    shutil.copyfile(DEPLOY_SCRIPT, script)
    script.chmod(0o755)

    (tmp_path / "backend" / "scripts").mkdir(parents=True)
    (tmp_path / "backend" / "scripts" / "install_backend_dependencies.py").write_text(
        "# fake installer handled by the fake interpreter\n", encoding="utf-8"
    )
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    _write_executable(tmp_path / ".venv" / "bin" / "python", FAKE_PYTHON)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "curl", FAKE_CURL)
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")


def _environment(tmp_path: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    for key in (
        "KOI_PORT",
        "KOI_HOST",
        "KOI_DATA_ROOT",
        "PARSER_ENGINE",
        "DOWNLOADER_ENGINE",
        "SKIP_DEPS",
        "SKIP_FRONTEND",
        "PIP_INDEX",
    ):
        environment.pop(key, None)
    environment["PATH"] = os.pathsep.join(
        [str(tmp_path / "bin"), environment.get("PATH", "")]
    )
    environment["TEST_ROOT"] = str(tmp_path)
    environment["SKIP_FRONTEND"] = "1"
    environment.update(extra_env or {})
    return environment


def stop_native(tmp_path: Path) -> None:
    if not (tmp_path / ".deploy-logs").exists():
        return
    subprocess.run(
        [BASH, str(tmp_path / "deploy-native.sh"), "stop"],
        cwd=tmp_path,
        env=_environment(tmp_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def run_native(
    tmp_path: Path,
    *,
    extra_env: dict[str, str] | None = None,
    stdin_text: str = "",
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [BASH, str(tmp_path / "deploy-native.sh")],
            cwd=tmp_path,
            env=_environment(tmp_path, extra_env),
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    finally:
        stop_native(tmp_path)


def start_native_with_open_stdin(
    tmp_path: Path,
) -> tuple[subprocess.Popen[str], object, object]:
    process = subprocess.Popen(
        [BASH, str(tmp_path / "deploy-native.sh")],
        cwd=tmp_path,
        env=_environment(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return process, process.stdin, process.stdout


def wait_for_output(output: object, needle: str) -> str:
    deadline = time.monotonic() + 5
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = output.readline()  # type: ignore[attr-defined]
        if not line:
            break
        lines.append(line)
        if needle in "".join(lines):
            return "".join(lines)
    raise AssertionError(f"did not observe {needle!r}; output was {''.join(lines)!r}")


def test_dotenv_is_loaded_as_default_but_process_environment_wins(tmp_path):
    write_fake_root(tmp_path, env_text="KOI_PORT=8123\nKOI_HOST=0.0.0.0\n")
    result = run_native(tmp_path, extra_env={"KOI_PORT": "8234"}, stdin_text="\n")
    assert result.returncode == 0
    assert "http://0.0.0.0:8234" in result.stdout
    assert "http://0.0.0.0:8123" not in result.stdout


def test_completed_dependency_marker_skips_installer(tmp_path):
    write_fake_root(tmp_path)
    marker = tmp_path / ".venv" / ".koifetch-deps-installed"
    marker.write_text("ok\n", encoding="utf-8")
    result = run_native(tmp_path, stdin_text="\n")
    assert result.returncode == 0
    assert not (tmp_path / "installer.count").exists()
    assert "skipping backend dependency installation" in result.stdout


def test_successful_dependency_install_creates_marker(tmp_path):
    write_fake_root(tmp_path)
    result = run_native(tmp_path, stdin_text="\n")
    assert result.returncode == 0
    assert (tmp_path / ".venv" / ".koifetch-deps-installed").is_file()
    assert (tmp_path / "installer.count").read_text(encoding="utf-8") == "1\n"


def test_health_success_waits_for_enter_before_start_returns(tmp_path):
    write_fake_root(tmp_path)
    process, stdin, output = start_native_with_open_stdin(tmp_path)
    try:
        wait_for_output(output, "healthy after")
        time.sleep(0.5)
        assert process.poll() is None
        stdin.write("\n")
        stdin.close()
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        stop_native(tmp_path)
