#!/usr/bin/env python3
"""Make musicdl 2.13.x work on Python 3.10 (upstream packaging bug).

musicdl 2.13.x imports ``Unpack`` from ``typing`` — a Python >= 3.11 name —
but declares no ``Requires-Python``, so on Python 3.10 importing the client
layer (``from musicdl import musicdl``) fails with
``ImportError: cannot import name 'Unpack' from 'typing'``.

Rather than rewriting third-party source (import syntax has many legal shapes
— parens, trailing commas, aliases, comments, backslash continuations — and a
rewriter can silently corrupt them), this script installs a tiny startup shim
into the ACTIVE environment's site-packages: a ``koi_typing_compat.py`` module
that backports ``typing.Unpack`` from ``typing_extensions`` on Python < 3.11,
plus a ``koi_typing_compat.pth`` line that imports it at interpreter startup
(before any package imports). The shim is purely additive and idempotent: two
new files, never touches existing code, and fixes ``from typing import Unpack``
for any package on 3.10, not just musicdl.

Idempotent: safe to run after every fresh install; a no-op when the shim is
already present. Not needed on Python >= 3.11.

    .venv/bin/python backend/scripts/patch_musicdl_py310.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

_SHIM_MODULE = "koi_typing_compat"
_SHIM_SOURCE = '''"""Backport typing.Unpack from typing_extensions on Python < 3.11.

Installed by backend/scripts/patch_musicdl_py310.py (see that module for why).
Loaded at interpreter startup via the sibling .pth file, before any package
imports, so ``from typing import Unpack`` works on Python 3.10 for musicdl
2.13.x and anything else that uses the name.
"""

import sys

if sys.version_info < (3, 11):
    import typing
    import typing_extensions

    if not hasattr(typing, "Unpack"):
        typing.Unpack = typing_extensions.Unpack
'''

_PTH_LINE = f"import {_SHIM_MODULE}\n"


def _site_packages() -> Path:
    return Path(sysconfig.get_paths()["purelib"]).resolve()


def install_shim(site_packages: Path) -> tuple[bool, bool]:
    """Write the shim module + .pth line; return (module_written, pth_written)."""
    site_packages.mkdir(parents=True, exist_ok=True)
    module_path = site_packages / f"{_SHIM_MODULE}.py"
    pth_path = site_packages / f"{_SHIM_MODULE}.pth"

    module_written = _write_if_changed(module_path, _SHIM_SOURCE)
    pth_written = _write_if_changed(pth_path, _PTH_LINE)
    return module_written, pth_written


def _write_if_changed(path: Path, content: str) -> bool:
    """Write ``content`` atomically; return True when the file changed."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the typing.Unpack backport shim for Python 3.10."
    )
    parser.add_argument(
        "--site-packages",
        type=Path,
        default=None,
        help="Site-packages dir to install into (default: the active environment's).",
    )
    args = parser.parse_args(argv)

    if sys.version_info >= (3, 11):
        print("Python >= 3.11: typing.Unpack exists; nothing to do.")
        return 0
    try:
        import typing_extensions  # noqa: F401  (needed by the shim at runtime)
    except ImportError:
        print(
            "typing_extensions is not installed; run: pip install typing-extensions",
            file=sys.stderr,
        )
        return 1

    site_packages = (args.site_packages or _site_packages()).resolve()
    try:
        module_written, pth_written = install_shim(site_packages)
    except OSError as exc:
        print(f"cannot write to {site_packages}: {exc}", file=sys.stderr)
        return 1
    print(
        f"{'installed' if module_written else 'present'} {_SHIM_MODULE}.py "
        f"{'installed' if pth_written else 'present'} {_SHIM_MODULE}.pth "
        f"in {site_packages}"
    )

    # End-to-end verification in a fresh interpreter. Production (no
    # --site-packages): the shim lives in the real site-packages and must be
    # active via its .pth at startup, so the typing check needs no explicit
    # import. Test mode (--site-packages): PYTHONPATH directories do not
    # process .pth files, so the shim is imported explicitly.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(site_packages) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    if args.site_packages is None:
        typing_check = (
            "import typing; assert hasattr(typing, 'Unpack'); "
            "from typing import Unpack; print('typing.Unpack ok')"
        )
    else:
        typing_check = (
            f"import {_SHIM_MODULE}; import typing; "
            "from typing import Unpack; print('typing.Unpack ok')"
        )
    probe = subprocess.run(
        [sys.executable, "-c", typing_check], capture_output=True, text=True, env=env
    )
    if probe.returncode != 0:
        print(f"shim verification failed:\n{probe.stderr}", file=sys.stderr)
        return 1
    print(probe.stdout.strip())

    musicdl_check = (
        f"import {_SHIM_MODULE}; from musicdl import musicdl; "
        "print('musicdl ok:', musicdl.MusicClient.__name__)"
    )
    probe = subprocess.run(
        [sys.executable, "-c", musicdl_check], capture_output=True, text=True, env=env
    )
    if probe.returncode != 0:
        if "No module named 'musicdl'" in probe.stderr:
            print("musicdl not installed; shim installed anyway.")
            return 0
        print(f"musicdl client import failed:\n{probe.stderr}", file=sys.stderr)
        return 1
    print(probe.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
