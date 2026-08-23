"""Local bubble/pond storage adapter.

Implements :class:`app.adapters.protocols.StorageAdapter` on the local
filesystem. Bubble roots are temporary storage (the file behind a short-lived
download link; cleaned by the worker when it expires), pond roots are
permanent storage (the PRD §3.3.6 NAS-path-like layout; the NAS API of Task 10
serves from here). A future S3/NAS adapter implements the same protocol.

**Absolute-root resolution rule (documented):** configured storage roots are
resolved to absolute paths at adapter-construction time via
:func:`resolve_storage_root` — a relative root resolves against the *process
working directory at construction*, an absolute root passes through unchanged
(symlinks normalized). This satisfies the domain ``build_path`` requirement
(absolute roots only) and makes containment unambiguous. ``Settings`` keeps
its relative defaults (``data/pond/video`` …) untouched; resolution happens
here, not in the settings contract.

**Containment (TOCTOU, per the Task 5 note):** ``build_path`` validates
containment when a path is *built*; every I/O operation here re-verifies the
candidate against the *current* roots right before touching the filesystem
(roots could be swapped or symlinks could appear between build and use).
Traversal attempts raise :class:`app.domain.paths.PathOutsideRootError`.

**Move semantics:** :meth:`save_file` and :meth:`move_to_pond` *move* (via
``shutil.move``, which handles cross-device by copy+delete), so the source
disappears. Callers that need the source kept should copy first.

**Listing:** :meth:`list_files` walks one bucket recursively and returns
sorted absolute paths of files (directories are skipped) — enough for the
preview and NAS APIs; this is deliberately not a full file browser.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.adapters.protocols import StorageAdapter
from app.domain import MediaType
from app.domain.paths import PathOutsideRootError, build_path, is_within

__all__ = ["LocalStorageAdapter", "resolve_storage_root"]


def resolve_storage_root(root: Path | str) -> Path:
    """Resolve a configured storage root to a canonical absolute path.

    Relative roots resolve against the process working directory at
    adapter-construction time; absolute roots pass through unchanged. The
    result is normalized (``..`` removed, symlinks resolved).
    """
    path = Path(root)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


class LocalStorageAdapter:
    """Filesystem :class:`StorageAdapter` with bubble/pond roots per media type."""

    def __init__(
        self,
        *,
        pond_video: Path | str,
        pond_image: Path | str,
        pond_music: Path | str,
        bubble_video: Path | str,
        bubble_image: Path | str,
        bubble_music: Path | str,
    ) -> None:
        """Resolve all six roots to absolute paths and create them.

        Roots are created eagerly so a misconfigured (unwritable) storage
        location fails fast at startup rather than at the first download.
        """
        self._pond = {
            MediaType.VIDEO: resolve_storage_root(pond_video),
            MediaType.IMAGE: resolve_storage_root(pond_image),
            MediaType.MUSIC: resolve_storage_root(pond_music),
        }
        self._bubble = {
            MediaType.VIDEO: resolve_storage_root(bubble_video),
            MediaType.IMAGE: resolve_storage_root(bubble_image),
            MediaType.MUSIC: resolve_storage_root(bubble_music),
        }
        for root in (*self._pond.values(), *self._bubble.values()):
            root.mkdir(parents=True, exist_ok=True)

    # -- roots -------------------------------------------------------------

    def bubble_root(self, media_type: MediaType) -> Path:
        return self._bubble[media_type]

    def pond_root(self, media_type: MediaType) -> Path:
        return self._pond[media_type]

    # -- path resolution (no I/O) -------------------------------------------

    def resolve_bubble(self, media_type: MediaType, *components: str) -> Path:
        return self._resolve(self._bubble[media_type], components)

    def resolve_pond(self, media_type: MediaType, *components: str) -> Path:
        return self._resolve(self._pond[media_type], components)

    @staticmethod
    def _resolve(root: Path, components: tuple[str, ...]) -> Path:
        # build_path validates containment now; I/O methods re-validate later.
        return build_path(root, *components)

    # -- persistence ---------------------------------------------------------

    def save_file(
        self,
        media_type: MediaType,
        source: Path | str,
        filename: str,
        *,
        to_pond: bool = False,
    ) -> Path:
        target = self._save_target(media_type, filename, to_pond)
        shutil.move(os.fspath(source), os.fspath(target))
        return target

    def save_bytes(
        self,
        media_type: MediaType,
        filename: str,
        data: bytes,
        *,
        to_pond: bool = False,
    ) -> Path:
        target = self._save_target(media_type, filename, to_pond)
        with target.open("wb") as out:
            out.write(data)
        return target

    def move_to_pond(self, media_type: MediaType, bubble_path: Path | str) -> Path:
        bubble_root = self._bubble[media_type]
        bubble = Path(bubble_path)
        # TOCTOU: re-verify the source is still inside the current bubble root.
        if not is_within(bubble_root, bubble):
            raise PathOutsideRootError(
                f"path {str(bubble)!r} is outside bubble root {str(bubble_root)!r}"
            )
        relative = os.path.relpath(bubble, bubble_root)
        pond_target = self._resolve(self._pond[media_type], (relative,))
        pond_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(os.fspath(bubble), os.fspath(pond_target))
        return pond_target

    def read_bytes(self, stored_path: Path | str) -> bytes:
        path = Path(stored_path)
        self._ensure_within_roots(path)
        with path.open("rb") as source:
            return source.read()

    def exists(self, stored_path: Path | str) -> bool:
        path = Path(stored_path)
        if not self._within_roots(path):
            return False
        return path.is_file()

    def delete(self, stored_path: Path | str) -> None:
        path = Path(stored_path)
        self._ensure_within_roots(path)
        path.unlink()  # raises FileNotFoundError when absent

    # -- listing (preview / NAS APIs) -----------------------------------------

    def list_files(self, media_type: MediaType, *, pond: bool = False) -> list[Path]:
        root = self._pond[media_type] if pond else self._bubble[media_type]
        return sorted(path for path in root.rglob("*") if path.is_file())

    # -- internals -------------------------------------------------------------

    def _save_target(
        self, media_type: MediaType, filename: str, to_pond: bool
    ) -> Path:
        target = (
            self.resolve_pond(media_type, filename)
            if to_pond
            else self.resolve_bubble(media_type, filename)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _all_roots(self) -> tuple[Path, ...]:
        return (*self._pond.values(), *self._bubble.values())

    def _within_roots(self, path: Path) -> bool:
        return any(is_within(root, path) for root in self._all_roots())

    def _ensure_within_roots(self, path: Path) -> None:
        if not self._within_roots(path):
            raise PathOutsideRootError(
                f"path {str(path)!r} is outside every configured storage root"
            )
