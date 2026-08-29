"""Build hash-pinned compatibility wheels for the engine packages.

The upstream ``musicdl`` and ``f2`` wheels are pure Python, so their package
code can be reused without running an unreviewed build.  This script downloads
the exact upstream wheel, verifies its SHA-256, rewrites only dependency
metadata that is incompatible with Koi Fetch's tested dependency set, and
rebuilds ``RECORD``.  It intentionally uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

MUSICDL_CRYPTOGRAPHY_REQUIREMENT = "cryptography<51,>=50.0.1"


@dataclass(frozen=True)
class WheelSpec:
    name: str
    version: str
    url: str
    sha256: str


WHEEL_SPECS = {
    "musicdl": WheelSpec(
        "musicdl",
        "2.13.6",
        "https://files.pythonhosted.org/packages/b7/af/3105639aa85fbeca2a517a05f47c882875e0dc813acb74189c77ce1db79d/musicdl-2.13.6-py3-none-any.whl",
        "501be2af53f7da7e3fd3f8aa273121ad5f83b0ec40df4729c1e12fc05fb63ea3",
    ),
    "f2": WheelSpec(
        "f2",
        "0.0.1.7",
        "https://files.pythonhosted.org/packages/25/5a/1662a88819443ff46e84f35c189e302938971e796656a9884fb4199c301c/f2-0.0.1.7-py3-none-any.whl",
        "e7a317cd43d88aeff320801c674e622f0e8826b391bc89ed5c120e273aa0c1b5",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(spec: WheelSpec, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / Path(spec.url).name
    if destination.is_file() and _sha256(destination) == spec.sha256:
        return destination
    if destination.exists():
        destination.unlink()
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(spec.url, timeout=120) as response, temporary.open("wb") as target:
            shutil.copyfileobj(response, target)
        actual = _sha256(temporary)
        if actual != spec.sha256:
            raise RuntimeError(
                f"SHA-256 mismatch for {spec.name}: expected {spec.sha256}, got {actual}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _rewrite_metadata(package: str, raw: bytes) -> bytes:
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    rewritten: list[str] = []
    for line in lines:
        if package == "f2" and line.startswith("Requires-Dist:"):
            continue
        if package == "musicdl" and line.startswith("Requires-Dist: cryptography"):
            ending = "\r\n" if line.endswith("\r\n") else "\n"
            line = f"Requires-Dist: {MUSICDL_CRYPTOGRAPHY_REQUIREMENT}{ending}"
        rewritten.append(line)
    return "".join(rewritten).encode("utf-8")


def _record_digest(raw: bytes) -> str:
    digest = hashlib.sha256(raw).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=")
    return f"sha256={encoded.decode('ascii')}"


def rewrite_wheel(
    source: Path,
    output_dir: Path,
    *,
    package: str | None = None,
    remove_dependencies: bool = False,
) -> Path:
    """Rewrite one pure-Python wheel and return the rebuilt wheel path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        entries = {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }
    metadata_name = next(name for name in entries if name.endswith(".dist-info/METADATA"))
    record_name = next(name for name in entries if name.endswith(".dist-info/RECORD"))
    package_name = package or metadata_name.split("/")[0].split("-")[0].lower()
    metadata = entries[metadata_name]
    if remove_dependencies:
        metadata = _rewrite_metadata("f2", metadata)
    elif package_name == "musicdl":
        metadata = _rewrite_metadata("musicdl", metadata)
    entries[metadata_name] = metadata

    record_rows: list[list[str]] = []
    for name in sorted(entries):
        if name == record_name:
            continue
        raw = entries[name]
        record_rows.append([name, _record_digest(raw), str(len(raw))])
    record_rows.append([record_name, "", ""])
    record_buffer = io.StringIO(newline="")
    csv.writer(record_buffer, lineterminator="\n").writerows(record_rows)
    entries[record_name] = record_buffer.getvalue().encode("utf-8")

    destination = output_dir / source.name
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(entries):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, entries[name])
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def build_compatible_wheels(output_dir: Path, cache_dir: Path | None = None) -> list[Path]:
    """Download, verify, and rebuild all compatibility wheels."""
    output_dir.mkdir(parents=True, exist_ok=True)
    owned_cache = cache_dir is None
    if owned_cache:
        cache_dir = Path(tempfile.mkdtemp(prefix="koifetch-wheel-cache-"))
    assert cache_dir is not None
    try:
        results = []
        for package, spec in WHEEL_SPECS.items():
            upstream = _download(spec, cache_dir)
            results.append(
                rewrite_wheel(
                    upstream,
                    output_dir,
                    package=package,
                    remove_dependencies=package == "f2",
                )
            )
        return results
    finally:
        if owned_cache:
            shutil.rmtree(cache_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    for wheel in build_compatible_wheels(args.output, args.cache):
        print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
