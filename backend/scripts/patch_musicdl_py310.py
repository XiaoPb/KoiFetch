#!/usr/bin/env python3
"""Patch musicdl 2.13.x for Python 3.10 (upstream packaging bug).

musicdl 2.13.x imports ``Unpack`` from ``typing`` — a Python >= 3.11 feature —
but declares no ``Requires-Python``, so on Python 3.10 importing the client
layer (``from musicdl import musicdl``) fails with
``ImportError: cannot import name 'Unpack' from 'typing'``. This script
rewrites every ``Unpack`` name out of ``typing`` imports in the ACTIVE
environment's installed musicdl package onto ``typing_extensions`` (already a
transitive dependency of this project's stack). It covers both the single-name
form (``from typing import Unpack``) and multi-name forms
(``from typing import Dict, Any, Unpack``), preserves line endings, writes
atomically, and verifies afterwards that no ``typing`` import still carries
``Unpack`` (exit 1 if any remain).

Idempotent: safe to run after every fresh install; a no-op when nothing needs
patching. Not needed on Python >= 3.11 (the script exits early there unless
``--package-dir`` is given explicitly, e.g. by tests).

    .venv/bin/python backend/scripts/patch_musicdl_py310.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TYPING_IMPORT = re.compile(r"^from typing import (.+)$")
_PATCHED_IMPORT = "from typing_extensions import Unpack"
_IMPORT_NAMES = re.compile(r",\s*")


def _split_names(import_body: str) -> list[str]:
    """Split an ``import`` name list, tolerating parenthesized lists."""
    return [name.strip().strip("()") for name in _IMPORT_NAMES.split(import_body) if name.strip()]


def _rewrite_text(text: str) -> str:
    """Rewrite ``Unpack`` out of ``typing`` imports, preserving line endings."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        newline = line[len(body):]
        match = _TYPING_IMPORT.match(body)
        if not match:
            out.append(line)
            continue
        names = _split_names(match.group(1))
        if "Unpack" not in names:
            out.append(line)
            continue
        remaining = [name for name in names if name != "Unpack"]
        if remaining:
            out.append(f"from typing import {', '.join(remaining)}{newline}")
        out.append(f"{_PATCHED_IMPORT}{newline}")
    return "".join(out)


def _unpack_typing_imports(package_dir: Path) -> list[Path]:
    """Files still importing ``Unpack`` from ``typing`` (post-condition check)."""
    bad: list[Path] = []
    for path in sorted(package_dir.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _TYPING_IMPORT.match(line.strip())
            if match and "Unpack" in _split_names(match.group(1)):
                bad.append(path)
                break
    return bad


def patch_package(package_dir: Path) -> tuple[int, int]:
    """Rewrite ``Unpack`` imports under ``package_dir``; return (changed, already)."""
    changed = 0
    already = 0
    for path in sorted(package_dir.rglob("*.py")):
        # newline="" disables universal-newline translation so CRLF files
        # (musicdl ships one) keep their line endings through the rewrite.
        with path.open("r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        rewritten = _rewrite_text(text)
        if rewritten != text:
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="") as fh:
                fh.write(rewritten)
            tmp.replace(path)
            changed += 1
        elif _PATCHED_IMPORT in text:
            already += 1
    return changed, already


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Patch musicdl 2.13.x for Python 3.10 (see module docstring)."
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=None,
        help="Explicit musicdl package dir (default: the active environment's installed musicdl).",
    )
    args = parser.parse_args(argv)

    if args.package_dir is not None:
        package_dir = args.package_dir.resolve()
        if not package_dir.is_dir():
            print(f"--package-dir is not a directory: {package_dir}", file=sys.stderr)
            return 1
    else:
        if sys.version_info >= (3, 11):
            print("Python >= 3.11: typing.Unpack exists; nothing to patch.")
            return 0
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
        if musicdl.__file__ is None:
            print(
                "cannot locate the musicdl package (namespace package?); use --package-dir",
                file=sys.stderr,
            )
            return 1
        package_dir = Path(musicdl.__file__).resolve().parent

    changed, already = patch_package(package_dir)
    print(f"patched {changed} file(s), {already} already patched, in {package_dir}")
    remaining = _unpack_typing_imports(package_dir)
    if remaining:
        print("ERROR: still importing Unpack from typing in:", file=sys.stderr)
        for path in remaining:
            print(f"  {path}", file=sys.stderr)
        return 1
    print("post-condition ok: no remaining 'from typing import ... Unpack'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
