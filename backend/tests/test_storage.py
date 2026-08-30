"""Tests for the local bubble/pond storage adapter (``app/adapters/storage_local.py``).

Covers: write/read round-trips, bubble→pond moves, containment enforcement
(including at open/write time, per the Task 5 TOCTOU note), the documented
absolute-root resolution rule, and the listing helpers Tasks 8/10 consume.
All fixtures live under pytest's ``tmp_path`` — never real storage roots.
"""

from pathlib import Path

import pytest

from app.adapters.storage_local import (
    STORAGE_MEDIA_TYPES,
    LocalStorageAdapter,
    resolve_storage_root,
)
from app.domain import MediaType
from app.domain.paths import PathOutsideRootError


def make_adapter(tmp_path: Path) -> LocalStorageAdapter:
    return LocalStorageAdapter(
        pond_video=tmp_path / "pond/video",
        pond_image=tmp_path / "pond/image",
        pond_music=tmp_path / "pond/music",
        bubble_video=tmp_path / "bubble/video",
        bubble_image=tmp_path / "bubble/image",
        bubble_music=tmp_path / "bubble/music",
    )


class TestRootResolution:
    """Documented rule: relative roots resolve against the process CWD at
    adapter-construction time; absolute roots pass through unchanged."""

    def test_relative_root_resolved_against_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolved = resolve_storage_root(Path("data/pond/video"))
        assert resolved.is_absolute()
        assert resolved == (tmp_path / "data" / "pond" / "video").resolve()

    def test_absolute_root_passes_through(self, tmp_path):
        root = tmp_path / "pond" / "video"
        assert resolve_storage_root(root) == root.resolve()

    def test_adapter_resolves_relative_roots_at_construction(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        adapter = LocalStorageAdapter(
            pond_video=Path("data/pond/video"),
            pond_image=Path("data/pond/image"),
            pond_music=Path("data/pond/music"),
            bubble_video=Path("data/bubble/video"),
            bubble_image=Path("data/bubble/image"),
            bubble_music=Path("data/bubble/music"),
        )
        assert adapter.pond_root(MediaType.VIDEO).is_absolute()
        assert adapter.pond_root(MediaType.VIDEO) == (
            tmp_path / "data" / "pond" / "video"
        ).resolve()
        # An absolute root is required for the domain build_path helper; a
        # resolved adapter must therefore be able to build and save paths.
        stored = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"x")
        assert stored.is_absolute()
        assert adapter.read_bytes(stored) == b"x"

    def test_roots_created_at_construction(self, tmp_path):
        adapter = make_adapter(tmp_path)
        for media_type in STORAGE_MEDIA_TYPES:
            assert adapter.bubble_root(media_type).is_dir()
            assert adapter.pond_root(media_type).is_dir()

    def test_live_photo_has_no_storage_bucket(self, tmp_path):
        adapter = make_adapter(tmp_path)

        assert MediaType.LIVE_PHOTO not in STORAGE_MEDIA_TYPES
        with pytest.raises(KeyError):
            adapter.bubble_root(MediaType.LIVE_PHOTO)
        with pytest.raises(KeyError):
            adapter.resolve_bubble(MediaType.LIVE_PHOTO, "clip.zip")
        with pytest.raises(KeyError):
            adapter.pond_root(MediaType.LIVE_PHOTO)
        with pytest.raises(KeyError):
            adapter.resolve_pond(MediaType.LIVE_PHOTO, "clip.zip")


class TestSaveReadRoundTrip:
    def test_save_bytes_round_trip_to_bubble(self, tmp_path):
        adapter = make_adapter(tmp_path)
        stored = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"hello world")
        assert stored == adapter.resolve_bubble(MediaType.VIDEO, "clip.mp4")
        assert stored.is_absolute()
        assert adapter.read_bytes(stored) == b"hello world"
        assert adapter.exists(stored)

    def test_save_bytes_to_pond(self, tmp_path):
        adapter = make_adapter(tmp_path)
        stored = adapter.save_bytes(MediaType.MUSIC, "song.mp3", b"audio", to_pond=True)
        assert stored == adapter.resolve_pond(MediaType.MUSIC, "song.mp3")
        assert adapter.read_bytes(stored) == b"audio"
        assert adapter.exists(stored)

    def test_save_bytes_creates_nested_directories(self, tmp_path):
        adapter = make_adapter(tmp_path)
        stored = adapter.save_bytes(MediaType.IMAGE, "2026/08/pic.jpg", b"img")
        assert adapter.read_bytes(stored) == b"img"

    def test_save_file_moves_source_into_storage(self, tmp_path):
        adapter = make_adapter(tmp_path)
        source = tmp_path / "staging" / "raw.bin"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"file payload")
        stored = adapter.save_file(MediaType.VIDEO, source, "final.bin")
        assert stored == adapter.resolve_bubble(MediaType.VIDEO, "final.bin")
        assert not source.exists()  # moved, not copied
        assert adapter.read_bytes(stored) == b"file payload"

    def test_save_file_to_pond(self, tmp_path):
        adapter = make_adapter(tmp_path)
        source = tmp_path / "raw.bin"
        source.write_bytes(b"payload")
        stored = adapter.save_file(MediaType.IMAGE, source, "pic.jpg", to_pond=True)
        assert stored == adapter.resolve_pond(MediaType.IMAGE, "pic.jpg")
        assert adapter.read_bytes(stored) == b"payload"

    def test_unknown_path_read_raises(self, tmp_path):
        adapter = make_adapter(tmp_path)
        with pytest.raises(FileNotFoundError):
            adapter.read_bytes(adapter.resolve_bubble(MediaType.VIDEO, "missing.mp4"))


class TestMoveBetweenBubbleAndPond:
    def test_move_to_pond_moves_file(self, tmp_path):
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"data")
        pond = adapter.move_to_pond(MediaType.VIDEO, bubble)
        assert pond == adapter.resolve_pond(MediaType.VIDEO, "clip.mp4")
        assert not bubble.exists()  # moved out of bubble
        assert adapter.read_bytes(pond) == b"data"

    def test_move_to_pond_rejects_non_bubble_source(self, tmp_path):
        adapter = make_adapter(tmp_path)
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"x")
        with pytest.raises(PathOutsideRootError):
            adapter.move_to_pond(MediaType.VIDEO, outside)

    def test_move_to_pond_rejects_directory_source(self, tmp_path):
        # Passing the bubble root itself (or any directory) would move the
        # whole tree; the adapter refuses non-file sources.
        adapter = make_adapter(tmp_path)
        with pytest.raises(ValueError):
            adapter.move_to_pond(MediaType.VIDEO, adapter.bubble_root(MediaType.VIDEO))


class TestMoveToPondTarget:
    """Task 10: ``move_to_pond(..., target=...)`` moves a bubble file to a
    caller-chosen pond-relative destination (dirs + filename) instead of
    mirroring the bubble's relative path. Containment rules stay in the
    adapter: the target must be relative and free of ``.``/``..`` parts."""

    def test_move_to_pond_with_target_directory(self, tmp_path):
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"data")
        pond = adapter.move_to_pond(MediaType.VIDEO, bubble, target="视频/抖音/clip.mp4")
        assert pond == adapter.resolve_pond(MediaType.VIDEO, "视频", "抖音", "clip.mp4")
        assert not bubble.exists()  # moved, not copied
        assert adapter.read_bytes(pond) == b"data"

    def test_move_to_pond_target_renames_file(self, tmp_path):
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.VIDEO, "old-name.mp4", b"data")
        pond = adapter.move_to_pond(MediaType.VIDEO, bubble, target="new-name.mp4")
        assert pond == adapter.resolve_pond(MediaType.VIDEO, "new-name.mp4")
        assert adapter.read_bytes(pond) == b"data"

    def test_move_to_pond_target_creates_nested_directories(self, tmp_path):
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.MUSIC, "song.mp3", b"audio")
        pond = adapter.move_to_pond(
            MediaType.MUSIC, bubble, target="专辑/2026/01/song.mp3"
        )
        assert pond == adapter.resolve_pond(MediaType.MUSIC, "专辑", "2026", "01", "song.mp3")
        assert adapter.read_bytes(pond) == b"audio"

    def test_move_to_pond_without_target_mirrors_bubble_layout(self, tmp_path):
        # Regression: the original contract (no target) still mirrors the
        # bubble's relative path under the pond root.
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.VIDEO, "sub/folder/clip.mp4", b"data")
        pond = adapter.move_to_pond(MediaType.VIDEO, bubble)
        assert pond == adapter.resolve_pond(MediaType.VIDEO, "sub", "folder", "clip.mp4")

    def test_move_to_pond_absolute_target_rejected(self, tmp_path):
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"data")
        with pytest.raises(PathOutsideRootError):
            adapter.move_to_pond(MediaType.VIDEO, bubble, target=str(tmp_path / "x.mp4"))
        assert adapter.exists(bubble)  # untouched

    def test_move_to_pond_dotdot_target_rejected(self, tmp_path):
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"data")
        for bad in ("../evil.mp4", "a/../evil.mp4", "./evil.mp4", "a/./evil.mp4"):
            with pytest.raises(PathOutsideRootError):
                adapter.move_to_pond(MediaType.VIDEO, bubble, target=bad)
        assert adapter.exists(bubble)


class TestContainment:
    """Traversal attempts are rejected both at path build and at I/O time."""

    @pytest.fixture
    def hostile_mkdir(self, tmp_path, monkeypatch):
        """Simulate a TOCTOU race: swap the freshly created ``evil`` parent
        directory for a symlink to an outside directory, so the adapter's
        next I/O would escape the root unless it re-verifies at write time."""
        outside = tmp_path / "outside"
        outside.mkdir()
        real_mkdir = Path.mkdir

        def _swap(self_path, *args, **kwargs):
            result = real_mkdir(self_path, *args, **kwargs)
            if self_path.name == "evil":
                self_path.rename(self_path.with_name("evil_real"))
                try:
                    self_path.symlink_to(outside, target_is_directory=True)
                except (OSError, NotImplementedError):
                    pytest.skip("symlinks not supported on this platform/filesystem")
            return result

        monkeypatch.setattr(Path, "mkdir", _swap)
        return outside

    def test_resolve_bubble_rejects_escape(self, tmp_path):
        adapter = make_adapter(tmp_path)
        with pytest.raises(PathOutsideRootError):
            adapter.resolve_bubble(MediaType.VIDEO, "..", "..", "etc")

    def test_resolve_pond_rejects_escape(self, tmp_path):
        adapter = make_adapter(tmp_path)
        with pytest.raises(PathOutsideRootError):
            adapter.resolve_pond(MediaType.VIDEO, "..", "secret")

    def test_resolve_rejects_absolute_component(self, tmp_path):
        adapter = make_adapter(tmp_path)
        with pytest.raises(PathOutsideRootError):
            adapter.resolve_bubble(MediaType.VIDEO, str(tmp_path / "elsewhere"))

    def test_save_bytes_rejects_traversal_filename(self, tmp_path):
        adapter = make_adapter(tmp_path)
        with pytest.raises(PathOutsideRootError):
            adapter.save_bytes(MediaType.VIDEO, "../../evil.bin", b"x")

    def test_save_file_rejects_traversal_filename(self, tmp_path):
        adapter = make_adapter(tmp_path)
        source = tmp_path / "raw.bin"
        source.write_bytes(b"x")
        with pytest.raises(PathOutsideRootError):
            adapter.save_file(MediaType.VIDEO, source, "../evil.bin")

    def test_save_rejects_symlinked_parent_escape(self, tmp_path):
        # A symlink inside the root pointing outside must never let a write
        # land outside (defense in depth: build-time and write-time checks).
        adapter = make_adapter(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        link = adapter.bubble_root(MediaType.VIDEO) / "evil"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform/filesystem")
        with pytest.raises(PathOutsideRootError):
            adapter.save_bytes(MediaType.VIDEO, "evil/pwned.bin", b"x")
        assert not (outside / "pwned.bin").exists()

    def test_save_bytes_rechecks_target_at_write_time(self, tmp_path, hostile_mkdir):
        # Regression for the TOCTOU note: a symlink planted between path
        # build and the actual write must be caught by the write-time
        # re-verification, not just by the build-time check.
        adapter = make_adapter(tmp_path)
        with pytest.raises(PathOutsideRootError):
            adapter.save_bytes(MediaType.VIDEO, "evil/pwned.bin", b"x")
        assert not (hostile_mkdir / "pwned.bin").exists()

    def test_save_file_rechecks_target_at_write_time(self, tmp_path, hostile_mkdir):
        adapter = make_adapter(tmp_path)
        source = tmp_path / "raw.bin"
        source.write_bytes(b"x")
        with pytest.raises(PathOutsideRootError):
            adapter.save_file(MediaType.VIDEO, source, "evil/pwned.bin")
        assert not (hostile_mkdir / "pwned.bin").exists()

    def test_move_to_pond_target_rechecks_at_write_time(self, tmp_path, hostile_mkdir):
        # Same TOCTOU rule as save_file: a symlink planted into a parent of
        # the move target between build and move must not redirect the write.
        adapter = make_adapter(tmp_path)
        bubble = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"data")
        with pytest.raises(PathOutsideRootError):
            adapter.move_to_pond(MediaType.VIDEO, bubble, target="evil/pwned.mp4")
        assert not (hostile_mkdir / "pwned.mp4").exists()
        assert adapter.exists(bubble)  # the bubble file is left untouched

    def test_read_bytes_rechecks_containment_at_open_time(self, tmp_path):
        # TOCTOU note (Task 5): containment is re-verified when the file is
        # opened, not just when the path was built.
        adapter = make_adapter(tmp_path)
        outside = tmp_path / "secret.txt"
        outside.write_text("s")
        with pytest.raises(PathOutsideRootError):
            adapter.read_bytes(outside)

    def test_delete_rechecks_containment(self, tmp_path):
        adapter = make_adapter(tmp_path)
        outside = tmp_path / "secret.txt"
        outside.write_text("s")
        with pytest.raises(PathOutsideRootError):
            adapter.delete(outside)

    def test_exists_returns_false_outside_roots(self, tmp_path):
        # Predicate semantics (documented on the protocol): exists() never
        # raises — paths outside every configured root are simply not "stored
        # files" — while read_bytes/delete raise PathOutsideRootError.
        adapter = make_adapter(tmp_path)
        outside = tmp_path / "elsewhere.txt"
        outside.write_text("x")
        assert adapter.exists(outside) is False

    def test_bubble_path_rejected_as_pond_source(self, tmp_path):
        # A pond-root path is not a valid bubble source for move_to_pond.
        adapter = make_adapter(tmp_path)
        pond = adapter.save_bytes(MediaType.VIDEO, "clip.mp4", b"x", to_pond=True)
        with pytest.raises(PathOutsideRootError):
            adapter.move_to_pond(MediaType.VIDEO, pond)


class TestDeleteAndList:
    def test_delete_removes_file(self, tmp_path):
        adapter = make_adapter(tmp_path)
        stored = adapter.save_bytes(MediaType.VIDEO, "x.bin", b"x")
        adapter.delete(stored)
        assert not adapter.exists(stored)
        assert not stored.exists()

    def test_delete_missing_file_raises(self, tmp_path):
        adapter = make_adapter(tmp_path)
        with pytest.raises(FileNotFoundError):
            adapter.delete(adapter.resolve_bubble(MediaType.VIDEO, "absent.bin"))

    def test_list_files_bubble_and_pond_separated(self, tmp_path):
        adapter = make_adapter(tmp_path)
        a = adapter.save_bytes(MediaType.VIDEO, "a.mp4", b"1")
        b = adapter.save_bytes(MediaType.VIDEO, "b.mp4", b"2")
        adapter.save_bytes(MediaType.IMAGE, "c.jpg", b"3")  # other bucket
        p = adapter.save_bytes(MediaType.VIDEO, "p.mp4", b"4", to_pond=True)

        assert adapter.list_files(MediaType.VIDEO) == sorted([a, b])
        assert adapter.list_files(MediaType.VIDEO, pond=True) == [p]
        assert adapter.list_files(MediaType.IMAGE) == [
            adapter.resolve_bubble(MediaType.IMAGE, "c.jpg")
        ]

    def test_list_files_skips_directories(self, tmp_path):
        adapter = make_adapter(tmp_path)
        adapter.save_bytes(MediaType.VIDEO, "sub/inside.mp4", b"1")
        files = adapter.list_files(MediaType.VIDEO)
        assert files == [adapter.resolve_bubble(MediaType.VIDEO, "sub/inside.mp4")]
