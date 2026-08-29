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
    backend_install = _index(text, "pip install -r backend/requirements.txt")
    audit_install = re.search(
        r"python -m pip install pip-audit==[0-9]+\.[0-9]+\.[0-9]+", text
    )
    assert audit_install, "pip-audit must be installed at a pinned version"
    python_audit = _index(text, "python -m pip_audit -r backend/requirements.txt")
    npm_ci = _index(text, "npm ci --prefix frontend")
    npm_audit = _index(text, "npm audit --prefix frontend --audit-level=high")
    frontend_tests = _index(text, "npm test --prefix frontend")
    frontend_build = _index(text, "npm run build --prefix frontend")

    assert backend_install < audit_install.start() < python_audit
    assert npm_ci < npm_audit < frontend_tests < frontend_build


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
        "python -m pip_audit -r backend/requirements.txt",
        "npm audit --prefix frontend --audit-level=high",
    )
    for phrase in required:
        assert phrase in text, f"operations runbook missing: {phrase}"
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" not in text


def test_release_checklist_has_executable_security_rows_without_secrets():
    text = RELEASE_FILE.read_text(encoding="utf-8")
    required = (
        "python -m pip_audit -r backend/requirements.txt",
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
