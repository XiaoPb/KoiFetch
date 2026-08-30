"""Install Koi Fetch dependencies with verified engine compatibility wheels."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGING_VERSION = "26.3"


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


def install(
    requirements: Path,
    wheel_cache: Path | None = None,
    constraints: Path | None = None,
    *,
    run_smoke: bool = True,
) -> None:
    """Install normal dependencies, then the verified compatibility wheels."""
    constraints = constraints or requirements.with_name("constraints.txt")
    if not constraints.is_file():
        raise FileNotFoundError(f"dependency constraints file is required: {constraints}")
    # The wheel builder uses packaging's standards-compliant parsers. Bootstrap
    # it before importing the builder so this script also works in a virgin venv.
    _pip("install", f"packaging=={PACKAGING_VERSION}")
    from build_compatible_wheels import build_compatible_wheels

    with tempfile.TemporaryDirectory(prefix="koifetch-install-") as temporary:
        work = Path(temporary)
        filtered = work / "requirements.txt"
        wheelhouse = work / "wheelhouse"
        _without_conflicting_engine_lines(requirements, filtered)
        wheels = build_compatible_wheels(wheelhouse, wheel_cache)
        _pip("install", "--upgrade", "pip==26.2.1")
        _pip("install", "-r", str(filtered), "-c", str(constraints))
        # Keep the rewritten Requires-Dist metadata active so a clean venv
        # receives musicdl's complete runtime dependency closure.
        _pip(
            "install",
            "--force-reinstall",
            "-c",
            str(constraints),
            *(str(wheel) for wheel in wheels),
        )
        # Equivalent to the ``pip check`` command, using this interpreter.
        _pip("check")
        patch_script = requirements.parent / "scripts" / "patch_musicdl_py310.py"
        if not patch_script.is_file():
            patch_script = Path(__file__).with_name("patch_musicdl_py310.py")
        subprocess.run([sys.executable, str(patch_script)], check=True)
        if run_smoke:
            app_root = requirements.parent / "app"
            if not app_root.is_dir():
                app_root = Path.cwd() / "app"
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(app_root.parent) + (
                os.pathsep + environment["PYTHONPATH"]
                if environment.get("PYTHONPATH")
                else ""
            )
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from musicdl import musicdl; import f2.exceptions, f2.utils.utils; "
                    "from app.adapters.safe_upstream import SafeUpstreamClient; "
                    "print(musicdl.MusicClient.__name__, SafeUpstreamClient.__name__)",
                ],
                check=True,
                env=environment,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "requirements.txt",
    )
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--constraints", type=Path)
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="skip app imports (used before app source is copied into the image)",
    )
    args = parser.parse_args()
    install(args.requirements, args.cache, args.constraints, run_smoke=not args.skip_smoke)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
