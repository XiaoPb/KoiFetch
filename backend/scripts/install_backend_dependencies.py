"""Install Koi Fetch dependencies with verified engine compatibility wheels."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from build_compatible_wheels import build_compatible_wheels


def _without_conflicting_engine_lines(requirements: Path, destination: Path) -> None:
    """Keep source requirements auditable but defer engines to our wheels."""
    lines = requirements.read_text(encoding="utf-8").splitlines(keepends=True)
    with destination.open("w", encoding="utf-8", newline="") as output:
        for line in lines:
            normalized = line.lstrip().lower()
            if normalized.startswith("musicdl") or normalized.startswith("f2"):
                continue
            output.write(line)


def _pip(*arguments: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", *arguments], check=True)


def install(requirements: Path, wheel_cache: Path | None = None) -> None:
    """Install normal dependencies, then the verified compatibility wheels."""
    with tempfile.TemporaryDirectory(prefix="koifetch-install-") as temporary:
        work = Path(temporary)
        filtered = work / "requirements.txt"
        wheelhouse = work / "wheelhouse"
        _without_conflicting_engine_lines(requirements, filtered)
        wheels = build_compatible_wheels(wheelhouse, wheel_cache)
        _pip("install", "--upgrade", "pip==26.2.1")
        _pip("install", "-r", str(filtered))
        _pip("install", "--force-reinstall", "--no-deps", *(str(wheel) for wheel in wheels))
        # Equivalent to the ``pip check`` command, using this interpreter.
        _pip("check")
        subprocess.run(
            [sys.executable, "-c", "import f2.exceptions, f2.utils.utils; import musicdl"],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "requirements.txt",
    )
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    install(args.requirements, args.cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
