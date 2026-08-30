"""Static contracts for the dependency-security CI gates and runbooks."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_FILE = REPO_ROOT / ".github" / "workflows" / "ci.yml"
OPERATIONS_FILE = REPO_ROOT / "OPERATIONS.md"
RELEASE_FILE = REPO_ROOT / "RELEASE-CHECKLIST.md"
DEPLOYMENT_FILE = REPO_ROOT / "docs" / "deployment.md"
README_FILE = REPO_ROOT / "README.md"


def _workflow_run_text() -> str:
    workflow = yaml.safe_load(CI_FILE.read_text(encoding="utf-8"))
    runs: list[str] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if isinstance(step, dict) and step.get("run"):
                runs.append(str(step["run"]))
    return "\n".join(runs)


def _index(text: str, needle: str) -> int:
    position = text.find(needle)
    assert position >= 0, f"missing CI command: {needle}"
    return position


def test_ci_audits_python_and_javascript_dependencies_in_gate_order():
    text = _workflow_run_text()
    backend_install = _index(
        text, "python backend/scripts/install_backend_dependencies.py"
    )
    audit_install = re.search(
        r"python -m pip install pip-audit==[0-9]+\.[0-9]+\.[0-9]+", text
    )
    assert audit_install, "pip-audit must be installed at a pinned version"
    python_audit = _index(text, "python -m pip_audit")
    npm_ci = _index(text, "npm ci --prefix frontend")
    npm_audit = _index(text, "npm audit --prefix frontend --audit-level=high")
    frontend_tests = _index(text, "npm test --prefix frontend")
    frontend_build = _index(text, "npm run build --prefix frontend")

    assert backend_install < audit_install.start() < python_audit
    assert npm_ci < npm_audit < frontend_tests < frontend_build
    assert "python -m pip_audit -r" not in text


def test_ci_keeps_engine_self_test_and_backend_gate_without_masking_failures():
    text = _workflow_run_text()
    for command in (
        "npm run check:engines --prefix frontend",
        "node --test frontend/scripts/check-engines.test.mjs",
        "python -m pytest backend/tests -q",
    ):
        assert command in text
    assert "continue-on-error" not in CI_FILE.read_text(encoding="utf-8")
    assert "|| true" not in text


def test_ci_runs_the_opt_in_clean_install_contract_separately():
    workflow = CI_FILE.read_text(encoding="utf-8")
    assert "KOIFETCH_RUN_LIVE_INSTALL: \"1\"" in workflow
    assert "python -m pytest backend/tests/test_compatible_wheels.py -m integration -q" in workflow


def test_operations_runbook_covers_cookie_migration_and_security_boundaries():
    text = OPERATIONS_FILE.read_text(encoding="utf-8")
    text = re.sub(r"\s+", " ", text)
    required = (
        "COOKIE_ENCRYPTION_KEY",
        "secrets.token_bytes(32)",
        "32-byte",
        "URL-safe base64",
        "loss makes cookies unreadable",
        "never log",
        "never commit",
        "python backend/scripts/encrypt_platform_cookies.py",
        "second run must report 0",
        "transactional",
        "do not rotate the key",
        "fails closed",
        "process-local",
        "multiply/isolate per worker",
        "external shared rate limiting",
        "TRUSTED_PROXY_CIDRS",
        "immediate controlled proxy",
        "ignores X-Forwarded-For",
        "SSRF",
        "http(s) only",
        "re-resolve",
        "pinned IP",
        "Host/SNI",
        "bounded redirects",
        "bounded body",
        "shared music/preview client",
        "no caller-supplied music URL",
        "^20.19.0 || ^22.13.0 || >=24.0.0",
        "python -m pip_audit",
        "npm audit --prefix frontend --audit-level=high",
        "install_backend_dependencies.py",
        "cryptography>=50.0.1,<51",
        "musicdl",
        "f2",
        "pip check",
    )
    for phrase in required:
        assert phrase in text, f"operations runbook missing: {phrase}"
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" not in text


def test_deployment_docs_use_the_unified_installer_and_current_runtime_contract():
    text = DEPLOYMENT_FILE.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", text)
    assert "backend/scripts/install_backend_dependencies.py" in text
    assert "pip install -r backend/requirements.txt" not in text
    assert "patch_musicdl_py310.py" not in text
    assert "^20.19.0 || ^22.13.0 || >=24.0.0" in normalized
    assert "COOKIE_ENCRYPTION_KEY" in text
    assert "backend/scripts/encrypt_platform_cookies.py" in text
    assert "expect 718+" not in text


def test_readme_install_guidance_uses_the_unified_backend_installer():
    text = README_FILE.read_text(encoding="utf-8")
    assert "backend/scripts/install_backend_dependencies.py" in text
    assert not re.search(r"pip\s+install\s+-r\s+[^\n]*requirements\.txt", text)
    assert "install `backend/requirements.txt`" not in text
    assert "COOKIE_ENCRYPTION_KEY" in text


def test_release_checklist_has_executable_security_rows_without_secrets():
    text = RELEASE_FILE.read_text(encoding="utf-8")
    required = (
        "python -m pip_audit",
        "npm audit --prefix frontend --audit-level=high",
        "python backend/scripts/encrypt_platform_cookies.py",
        "backup DB and COOKIE_ENCRYPTION_KEY",
        "second run must report 0",
        "weak/default secret rejection",
        "test_config.py",
        "test_login_limiter.py",
        "TRUSTED_PROXY_CIDRS",
        "SSRF regression",
        "test_safe_upstream.py",
    )
    for phrase in required:
        assert phrase in text, f"release checklist missing: {phrase}"
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" not in text


def test_release_docs_use_dynamic_statuses_and_current_runtime_scope():
    operations = OPERATIONS_FILE.read_text(encoding="utf-8")
    release = RELEASE_FILE.read_text(encoding="utf-8")
    assert "Deliberately absent" not in operations
    assert "deterministic stubs" not in operations
    for stale in ("637 passed", "147 passed", "Docker is not installed"):
        assert stale not in release
    assert "Exit 0; no test failures" in release
