#!/usr/bin/env python3
"""Patch musicdl 2.13.x for Python 3.10 (upstream packaging bug).

musicdl 2.13.x imports ``Unpack`` from ``typing`` — a Python >= 3.11 feature —
but declares no ``Requires-Python``, so on Python 3.10 importing the client
layer (``from musicdl import musicdl``) fails with
``ImportError: cannot import name 'Unpack' from 'typing'``. This script
rewrites every ``Unpack`` name out of ``typing`` imports in the ACTIVE
environment's installed musicdl package onto ``typing_extensions`` (already a
transitive dependency of this project's stack). It covers single-line imports
in both plain (``from typing import Unpack``) and multi-name
(``from typing import Dict, Any, Unpack``) and aliased
(``from typing import Unpack as U``) forms, normalizes parenthesized
single-line lists (``from typing import (Dict, Unpack)``), preserves line
endings, writes atomically, and afterwards verifies no ``typing`` import
still carries ``Unpack`` (exit 1 if any remain). Parenthesized multi-line and
backslash-continuation imports are NOT auto-rewritten; the post-condition
detects and reports them loudly instead of silently missing them.

Idempotent: safe to run after every fresh install; a no-op when nothing needs
patching. Files with no ``Unpack`` usage at all are left untouched and are not
counted. Not needed on Python >= 3.11 (the script exits early there unless
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
_IMPORT_NAMES_SPLIT = re.compile(r",\s*")


def _base_name(name: str) -> str:
    """The imported name behind an alias (``Unpack as U`` -> ``Unpack``).

    Strips surrounding parens and commas so callers can feed raw fragments
    (``(Unpack,``, ``Unpack)``, ``Unpack,``) safely.
    """
    return name.split(" as ", 1)[0].strip().strip("(),")


def _split_names(import_body: str) -> list[str]:
    """Split an ``import`` name list on commas."""
    return [
        name.strip() for name in _IMPORT_NAMES_SPLIT.split(import_body) if name.strip()
    ]


def _rewrite_text(text: str) -> str:
    """Rewrite ``Unpack`` out of single-line ``typing`` imports.

    Line endings are preserved; a statement that ends the file without a
    newline is terminated with ``\\n`` so the rewritten lines never fuse.
    Aliased imports (``from typing import Unpack as U``) keep their alias,
    and parenthesized single-line lists (``from typing import (Dict, Unpack)``)
    are normalized to a plain kept-name import.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        newline = line[len(body):]
        match = _TYPING_IMPORT.match(body)
        if not match:
            out.append(line)
            continue
        if "(" in body and ")" not in body or body.endswith("\\"):
            # Opening line of a parenthesized multi-line block, or a
            # backslash-continuation import: never rewrite these in isolation
            # (it would mangle the statement). The post-condition reports
            # such forms loudly.
            out.append(line)
            continue
        import_body = match.group(1).split("#", 1)[0].strip("()")
        names = _split_names(import_body)
        removed = [name for name in names if _base_name(name) == "Unpack"]
        if not removed:
            out.append(line)
            continue
        kept = [name for name in names if _base_name(name) != "Unpack"]
        alias = removed[0].split(" as ", 1)[1].strip() if " as " in removed[0] else None
        import_line = (
            _PATCHED_IMPORT if alias is None else f"from typing_extensions import Unpack as {alias}"
        )
        sep = newline or "\n"  # never fuse the rewritten lines
        if kept:
            out.append(f"from typing import {', '.join(name.strip('(),') for name in kept)}{sep}")
        out.append(f"{import_line}{sep}")
    return "".join(out)


def _unpack_typing_imports(package_dir: Path) -> list[Path]:
    """Files still importing ``Unpack`` from ``typing`` (post-condition check).

    Detects single-line forms (plain, aliased, parenthesized, with trailing
    commas or inline comments) and parenthesized multi-line blocks — including
    names on the opening line, the closing paren on a name line, and inline
    comments — plus backslash-continuation imports. These forms are not all
    auto-rewritten; any remaining one fails the check loudly instead of being
    silently missed.
    """
    bad: list[Path] = []
    for path in sorted(package_dir.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        in_paren_import = False
        in_backslash_import = False
        for line in lines:
            stripped = line.strip()
            if in_paren_import:
                # content before any inline comment, then before any closing paren
                content = stripped.split("#", 1)[0].strip()
                closing = content.find(")")
                candidate = content[:closing] if closing != -1 else content
                if closing != -1:
                    in_paren_import = False
                if _base_name(candidate.rstrip(",")) == "Unpack":
                    bad.append(path)
                    break
                continue
            if in_backslash_import:
                in_backslash_import = False
                if _base_name(stripped.rstrip(",")) == "Unpack":
                    bad.append(path)
                    break
                if stripped.endswith("\\"):
                    in_backslash_import = True
                continue
            match = _TYPING_IMPORT.match(stripped)
            if not match:
                continue
            import_body = match.group(1).split("#", 1)[0].strip("()")
            names = _split_names(import_body)
            if any(_base_name(name) == "Unpack" for name in names):
                bad.append(path)
                break
            if "(" in stripped and ")" not in stripped:
                in_paren_import = True
                continue
            if stripped.endswith("\\"):
                in_backslash_import = True
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
