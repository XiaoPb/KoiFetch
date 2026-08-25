#!/usr/bin/env python3
"""Patch musicdl 2.13.x for Python 3.10 (upstream packaging bug).

musicdl 2.13.x imports ``Unpack`` from ``typing`` — a Python >= 3.11 feature —
but declares no ``Requires-Python``, so on Python 3.10 ``import musicdl`` fails
with ``ImportError: cannot import name 'Unpack' from 'typing'``. This script
rewrites those imports to ``typing_extensions`` (already a transitive dependency
of this project's stack) in the ACTIVE environment's installed musicdl package.

Idempotent: safe to run after every fresh install; a no-op when nothing needs
patching. Not needed on Python >= 3.11. Run from anywhere with the project
venv active:

    .venv/bin/python backend/scripts/patch_musicdl_py310.py
"""

from __future__ import annotations

import sys
from pathlib import Path

IMPORT_OLD = "from typing import Unpack"
IMPORT_NEW = "from typing_extensions import Unpack"


def main() -> int:
    try:
        import typing_extensions  # noqa: F401
    except ImportError:
        print(
            "typing_extensions is not installed; run: pip install typing-extensions",
            file=sys.stderr,
        )
        return 1
    try:
        import musicdl
    except ImportError:
        print("musicdl is not installed; nothing to patch", file=sys.stderr)
        return 0

    package_dir = Path(musicdl.__file__).resolve().parent
    changed: list[str] = []
    already = 0
    for path in sorted(package_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if IMPORT_OLD in text:
            path.write_text(text.replace(IMPORT_OLD, IMPORT_NEW), encoding="utf-8")
            changed.append(str(path))
        elif IMPORT_NEW in text:
            already += 1

    print(
        f"patched {len(changed)} file(s), {already} already patched, "
        f"in {package_dir}"
    )
    for name in changed:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
