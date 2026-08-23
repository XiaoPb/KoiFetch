"""Tests for the local bubble/pond storage adapter (``app/adapters/storage_local.py``).

Covers: write/read round-trips, bubble→pond moves, containment enforcement
(including at open/write time, per the Task 5 TOCTOU note), the documented
absolute-root resolution rule, and the listing helpers Tasks 8/10 consume.
All fixtures live under pytest's ``tmp_path`` — never real storage roots.
"""

from pathlib import Path

import pytest

from app.adapters.storage_local import LocalStorageAdapter, resolve_storage_root
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
        for media_type in MediaType:
            assert adapter.bubble_root(media_type).is_dir()
            assert adapter.pond_root(media_type).is_dir()


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


class TestContainment:
    """Traversal attempts are rejected both at path build and at I/O time."""

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
