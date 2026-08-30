"""Run the backend dependency audit with a fail-closed review contract."""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
from collections.abc import Iterable
from typing import Any

from packaging.utils import canonicalize_name

PARSE_VIDEO_URL = "https://github.com/wujunwei928/parse-video-py.git"
PARSE_VIDEO_COMMIT = "5fcf87256edb5ffcdebf0e4aac2a5a41745da76e"
ALLOWED_AUDIT_SKIPS = frozenset({"parse-video-py"})


def _direct_url(distribution: Any) -> dict[str, Any] | None:
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid direct_url.json for {distribution.metadata['Name']}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid direct_url.json for {distribution.metadata['Name']}")
    return value


def validate_vcs_installs(distributions: Iterable[Any] | None = None) -> None:
    """Require the one explicitly reviewed VCS dependency, at one exact commit."""
    if distributions is None:
        distributions = importlib.metadata.distributions()
    found: dict[str, tuple[str, str, str | None]] = {}
    for distribution in distributions:
        direct_url = _direct_url(distribution)
        if not direct_url or "vcs_info" not in direct_url:
            continue
        name = distribution.metadata.get("Name")
        vcs_info = direct_url.get("vcs_info")
        if not name or not isinstance(vcs_info, dict):
            raise RuntimeError("malformed VCS direct_url metadata")
        found[canonicalize_name(name)] = (
            str(direct_url.get("url", "")),
            str(vcs_info.get("vcs", "")),
            vcs_info.get("commit_id"),
        )
    expected = {
        canonicalize_name("parse-video-py"): (PARSE_VIDEO_URL, "git", PARSE_VIDEO_COMMIT)
    }
    if set(found) != set(expected):
        raise RuntimeError(
            f"unexpected VCS dependency set: {sorted(found)}; expected {sorted(expected)}"
        )
    actual = found[canonicalize_name("parse-video-py")]
    if actual != expected[canonicalize_name("parse-video-py")]:
        raise RuntimeError(f"parse-video-py VCS source is not the approved URL/commit: {actual}")


def validate_audit_payload(payload: Any) -> None:
    """Reject newly skipped/unknown packages in pip-audit's JSON result."""
    dependencies = payload.get("dependencies") if isinstance(payload, dict) else payload
    if not isinstance(dependencies, list):
        raise RuntimeError("pip-audit JSON has no dependency list")
    skipped: set[str] = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise RuntimeError("pip-audit returned a malformed dependency entry")
        if dependency.get("skip_reason") is not None:
            name = dependency.get("name")
            if not isinstance(name, str) or not name:
                raise RuntimeError("pip-audit returned a skipped dependency without a name")
            skipped.add(canonicalize_name(name))
    if skipped != {canonicalize_name(name) for name in ALLOWED_AUDIT_SKIPS}:
        raise RuntimeError(
            f"unexpected pip-audit skip set: {sorted(skipped)}; "
            f"expected {sorted(ALLOWED_AUDIT_SKIPS)}"
        )


def main() -> int:
    try:
        validate_vcs_installs()
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--format", "json", "--progress-spinner", "off"],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("pip-audit did not produce valid JSON") from exc
        validate_audit_payload(payload)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    except RuntimeError as exc:
        print(f"dependency audit contract failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
