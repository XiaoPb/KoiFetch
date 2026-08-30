"""Build hash-pinned compatibility wheels for the engine packages.

The upstream ``musicdl`` and ``f2`` wheels are pure Python, so their package
code can be reused without running an unreviewed build.  This script downloads
the exact upstream wheel, verifies its SHA-256, rewrites only dependency
metadata that is incompatible with Koi Fetch's tested dependency set, and
rebuilds ``RECORD``.  ``packaging`` is used for standards-compliant wheel and
requirement parsing and is installed explicitly by the backend installer.
"""

from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
from email.policy import compat32
import hashlib
import io
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

MUSICDL_CRYPTOGRAPHY_REQUIREMENT = "cryptography<51,>=50.0.1"

_F2_COMPAT_REQUIREMENTS = {
    "aiofiles": "aiofiles>=24.1.0",
    "aiosqlite": "aiosqlite>=0.20.0",
    "browser-cookie3": "browser-cookie3>=0.20.1",
    "click": "click<9,>=8.1.7",
    "cryptography": "cryptography<51,>=50.0.1",
    "gmssl": "gmssl>=3.2.2",
    "httpx": "httpx>=0.27",
    "importlib-resources": "importlib-resources>=6.4.5",
    "jsonpath-ng": "jsonpath-ng>=1.6.1",
    "m3u8": "m3u8<7,>=6.0.0",
    "protobuf": "protobuf>=6.33.0",
    "pydantic": "pydantic>=2.9",
    "pyexecjs": "pyexecjs>=1.5.1",
    "pyyaml": "PyYAML>=6.0",
    "qrcode": "qrcode>=8.0",
    "rich": "rich<15,>=13.9.3",
    "websockets-proxy": "websockets-proxy>=0.1.2",
    "websockets": "websockets>=12.0",
}
_F2_DEV_REQUIREMENTS = {"babel", "black", "pytest", "pytest-asyncio"}


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


def _metadata_message(raw: bytes):
    try:
        message = BytesParser(policy=compat32).parsebytes(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("invalid wheel metadata") from exc
    if message.defects:
        raise RuntimeError(f"invalid wheel metadata defects: {message.defects[0]}")
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1 or not names[0].strip():
        raise RuntimeError("wheel metadata must contain exactly one Name and Version")
    try:
        Version(versions[0].strip())
    except InvalidVersion as exc:
        raise RuntimeError("invalid wheel metadata Version") from exc
    return message, names[0].strip(), versions[0].strip()


def _requirement(text: str, *, reject_semantics: bool = False) -> Requirement:
    try:
        requirement = Requirement(text)
    except InvalidRequirement as exc:
        raise RuntimeError(f"invalid wheel dependency requirement: {text}") from exc
    if reject_semantics and (requirement.extras or requirement.marker is not None):
        raise RuntimeError(
            "wheel dependency extras or markers are unsupported for compatibility rewrite"
        )
    return requirement


def _rewrite_metadata(
    package: str,
    raw: bytes,
    *,
    expected_name: str | None = None,
    expected_version: str | None = None,
) -> bytes:
    _message, metadata_name, metadata_version = _metadata_message(raw)
    if expected_name is not None and canonicalize_name(metadata_name) != canonicalize_name(
        expected_name
    ):
        raise RuntimeError(
            f"wheel metadata Name {metadata_name!r} does not match {expected_name!r}"
        )
    if expected_version is not None and Version(metadata_version) != Version(expected_version):
        raise RuntimeError(
            f"wheel metadata Version {metadata_version!r} does not match {expected_version!r}"
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("wheel metadata is not valid UTF-8") from exc
    lines = text.splitlines(keepends=True)
    rewritten: list[str] = []
    previous_requires_dist = False
    for line in lines:
        if previous_requires_dist and line[:1].isspace():
            raise RuntimeError("folded Requires-Dist metadata is unsupported")
        previous_requires_dist = False
        if line.lower().startswith("requires-dist:"):
            requirement = _requirement(
                line.partition(":")[2].strip(), reject_semantics=package == "f2"
            )
            dependency_name = canonicalize_name(requirement.name)
            replacement = _F2_COMPAT_REQUIREMENTS.get(dependency_name)
            previous_requires_dist = True
            if package == "f2":
                # Require every f2 runtime dependency to be explicitly reviewed.
                if replacement is None:
                    if dependency_name in _F2_DEV_REQUIREMENTS:
                        continue
                    raise RuntimeError(
                        f"unreviewed f2 runtime dependency: {dependency_name}"
                    )
                line = f"Requires-Dist: {replacement}\n"
            elif package == "musicdl" and dependency_name == "cryptography":
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                extras = (
                    "[" + ",".join(sorted(requirement.extras)) + "]"
                    if requirement.extras
                    else ""
                )
                marker = f"; {requirement.marker}" if requirement.marker else ""
                line = f"Requires-Dist: cryptography{extras}<51,>=50.0.1{marker}{ending}"
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
    expected_spec: WheelSpec | None = None,
) -> Path:
    """Rewrite one pure-Python wheel and return the rebuilt wheel path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("duplicate wheel entry")
        for name in names:
            parts = name.split("/")
            if (
                not name
                or name.startswith(("/", "\\"))
                or name[:2].isalpha() and name[2:3] == ":"
                or "\\" in name
                or ".." in parts
            ):
                raise RuntimeError(f"unsafe wheel entry path: {name!r}")
        entries = {
            name: archive.read(name)
            for name in names
            if not name.endswith("/")
        }
    dist_info_dirs = {
        "/".join(name.split("/")[: index + 1])
        for name in names
        for index, part in enumerate(name.split("/"))
        if part.endswith(".dist-info")
    }
    if len(dist_info_dirs) != 1:
        raise RuntimeError("wheel must contain exactly one dist-info directory")
    metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
    record_names = [name for name in names if name.endswith(".dist-info/RECORD")]
    if len(metadata_names) != 1:
        raise RuntimeError("wheel must contain exactly one METADATA")
    if len(record_names) != 1:
        raise RuntimeError("wheel must contain exactly one RECORD")
    metadata_name = metadata_names[0]
    record_name = record_names[0]
    package_name = package or metadata_name.split("/")[0].split("-")[0].lower()
    _message, metadata_identity_name, metadata_identity_version = _metadata_message(
        entries[metadata_name]
    )
    if expected_spec is not None:
        expected_filename = (
            f"{expected_spec.name}-{expected_spec.version}-py3-none-any.whl"
        )
        if source.name != expected_filename:
            raise RuntimeError(
                f"compatibility wheel filename must be {expected_filename}, got {source.name}"
            )
        try:
            wheel_name, wheel_version, _build, tags = parse_wheel_filename(source.name)
        except InvalidWheelFilename as exc:
            raise RuntimeError(f"invalid compatibility wheel filename: {source.name}") from exc
        if canonicalize_name(str(wheel_name)) != canonicalize_name(expected_spec.name):
            raise RuntimeError("wheel filename distribution does not match expected package")
        if Version(str(wheel_version)) != Version(expected_spec.version):
            raise RuntimeError("wheel filename version does not match expected package")
        if {str(tag) for tag in tags} != {"py3-none-any"}:
            raise RuntimeError("compatibility wheel must be tagged py3-none-any")
        if canonicalize_name(metadata_identity_name) != canonicalize_name(expected_spec.name):
            raise RuntimeError(
                f"wheel metadata Name {metadata_identity_name!r} does not match "
                f"{expected_spec.name!r}"
            )
        if Version(metadata_identity_version) != Version(expected_spec.version):
            raise RuntimeError("wheel metadata Version does not match expected package")
        dist_info_stem = Path(next(iter(dist_info_dirs))).name.removesuffix(".dist-info")
        if canonicalize_name(dist_info_stem.rsplit("-", 1)[0]) != canonicalize_name(
            expected_spec.name
        ):
            raise RuntimeError("wheel dist-info distribution does not match expected package")
    metadata = entries[metadata_name]
    if remove_dependencies:
        metadata = _rewrite_metadata(
            "f2",
            metadata,
            expected_name=expected_spec.name if expected_spec else None,
            expected_version=expected_spec.version if expected_spec else None,
        )
    elif package_name == "musicdl":
        metadata = _rewrite_metadata(
            "musicdl",
            metadata,
            expected_name=expected_spec.name if expected_spec else None,
            expected_version=expected_spec.version if expected_spec else None,
        )
    else:
        _metadata_message(metadata)
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
                    expected_spec=spec,
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
