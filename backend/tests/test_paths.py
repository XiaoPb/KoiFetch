"""Tests for the domain path/filename helpers (``app/domain/paths.py``).

Covers: slugify charset/length guarantees, PRD-style media filename building,
root containment (``is_within``) including symlink escapes, and the
root-scoped path builder (``build_path``). All filesystem fixtures live under
pytest's ``tmp_path`` — never real storage roots.
"""

from datetime import date, datetime
from pathlib import Path

import pytest

from app.domain.enums import MediaType
from app.domain.paths import (
    PathOutsideRootError,
    build_path,
    is_within,
    safe_media_filename,
    slugify,
)

SAFE_CHARSET = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


class TestSlugify:
    def test_lowercases_and_replaces_separators(self):
        assert slugify("Hello, World!") == "hello-world"

    def test_hyphens_and_underscores_are_separators(self):
        assert slugify("a-b_c1") == "a-b-c1"

    def test_collapses_separator_runs(self):
        assert slugify("a--b__c  d") == "a-b-c-d"

    def test_strips_leading_and_trailing_separators(self):
        assert slugify("-- hello --") == "hello"

    def test_empty_string_falls_back(self):
        assert slugify("") == "untitled"

    def test_custom_fallback(self):
        assert slugify("!!!", fallback="unknown") == "unknown"

    def test_non_ascii_is_dropped(self):
        # Chinese/emoji are not in the ASCII slug charset -> dropped entirely.
        assert slugify("晴天 示例") == "untitled"

    def test_accents_decompose_to_ascii(self):
        assert slugify("café") == "cafe"

    def test_null_bytes_neutralized(self):
        assert "\x00" not in slugify("a\x00b")

    def test_traversal_attempts_neutralized(self):
        assert slugify("../../etc/passwd") == "etc-passwd"
        assert "/" not in slugify("/etc/passwd")
        assert "\\" not in slugify("C:\\Windows\\system32")
        assert slugify("..") == "untitled"

    def test_result_always_safe_charset(self):
        nasty = "a/\\..:\x00\t\n'\"%$#@!()[]{}中文🎉 b"
        result = slugify(nasty)
        assert result == "a-b"
        assert set(result) <= SAFE_CHARSET

    def test_max_length_enforced(self):
        assert len(slugify("x" * 300)) <= 120

    def test_truncation_is_deterministic(self):
        assert slugify("x" * 200) == slugify("x" * 200)

    def test_truncated_slugs_stay_unique_for_distinct_inputs(self):
        assert slugify("a" * 200) != slugify("b" * 200)

    def test_short_input_untouched(self):
        assert slugify("hello") == "hello"

    def test_tiny_max_length_still_bounded(self):
        assert len(slugify("abcdefghij", max_length=5)) <= 5

    def test_invalid_max_length_rejected(self):
        with pytest.raises(ValueError):
            slugify("hello", max_length=0)


class TestSafeMediaFilename:
    def test_video_pattern(self):
        name = safe_media_filename(
            MediaType.VIDEO,
            published_at=date(2026, 8, 23),
            title="My Awesome Video",
            source_id="v12345",
            ext="mp4",
        )
        assert name == "2026-08-23_my-awesome-video_v12345.mp4"

    def test_music_uses_same_pattern(self):
        # Chinese title is dropped by slugify; source_id keeps the file unique.
        name = safe_media_filename(
            MediaType.MUSIC,
            published_at=date(2026, 8, 23),
            title="晴天 - 周杰伦",
            source_id="m678",
            ext="flac",
        )
        assert name == "2026-08-23_untitled_m678.flac"

    def test_image_numbered_pattern(self):
        name = safe_media_filename(
            MediaType.IMAGE,
            published_at=date(2026, 8, 23),
            title="Trip Photos",
            index=3,
            ext="jpg",
        )
        assert name == "003_trip-photos.jpg"

    def test_image_pads_index_to_three_digits(self):
        name = safe_media_filename(
            MediaType.IMAGE,
            published_at=date(2026, 8, 23),
            title="x",
            index=42,
            ext="png",
        )
        assert name == "042_x.png"

    def test_image_requires_index(self):
        with pytest.raises(ValueError):
            safe_media_filename(
                MediaType.IMAGE, published_at=date(2026, 8, 23), title="x", ext="jpg"
            )

    def test_video_requires_source_id(self):
        with pytest.raises(ValueError):
            safe_media_filename(
                MediaType.VIDEO, published_at=date(2026, 8, 23), title="x", ext="mp4"
            )

    def test_extension_sanitized(self):
        name = safe_media_filename(
            MediaType.VIDEO,
            published_at=date(2026, 8, 23),
            title="x",
            source_id="s",
            ext=".MP4 ",
        )
        assert name == "2026-08-23_x_s.mp4"

    def test_extension_traversal_neutralized(self):
        name = safe_media_filename(
            MediaType.VIDEO,
            published_at=date(2026, 8, 23),
            title="x",
            source_id="s",
            ext="../mp4",
        )
        assert name == "2026-08-23_x_s.mp4"

    def test_title_traversal_neutralized(self):
        name = safe_media_filename(
            MediaType.VIDEO,
            published_at=date(2026, 8, 23),
            title="../../evil",
            source_id="s",
            ext="mp4",
        )
        assert name == "2026-08-23_evil_s.mp4"

    def test_empty_extension_rejected(self):
        with pytest.raises(ValueError):
            safe_media_filename(
                MediaType.VIDEO,
                published_at=date(2026, 8, 23),
                title="x",
                source_id="s",
                ext="...",
            )

    def test_published_at_from_iso_string(self):
        name = safe_media_filename(
            MediaType.VIDEO,
            published_at="2026-08-23",
            title="x",
            source_id="s",
            ext="mp4",
        )
        assert name == "2026-08-23_x_s.mp4"

    def test_published_at_from_datetime(self):
        name = safe_media_filename(
            MediaType.VIDEO,
            published_at=datetime(2026, 8, 23, 12, 30),
            title="x",
            source_id="s",
            ext="mp4",
        )
        assert name == "2026-08-23_x_s.mp4"

    def test_invalid_published_at_rejected(self):
        with pytest.raises(ValueError):
            safe_media_filename(
                MediaType.VIDEO,
                published_at="not-a-date",
                title="x",
                source_id="s",
                ext="mp4",
            )


class TestIsWithin:
    def test_nested_path_allowed(self, tmp_path):
        root = tmp_path / "pond"
        candidate = root / "video" / "2026" / "file.mp4"
        assert is_within(root, candidate) is True

    def test_root_itself_is_within(self, tmp_path):
        assert is_within(tmp_path, tmp_path) is True

    def test_sibling_path_rejected(self, tmp_path):
        root = tmp_path / "a"
        other = tmp_path / "b"
        assert is_within(root, other) is False

    def test_parent_escape_rejected(self, tmp_path):
        root = tmp_path / "a" / "b"
        escape = tmp_path / "a" / "b" / ".." / ".." / "secret.txt"
        assert is_within(root, escape) is False

    def test_absolute_path_outside_root_rejected(self, tmp_path):
        root = tmp_path / "pond"
        outside = tmp_path / "elsewhere" / "file.mp4"
        assert is_within(root, outside) is False

    def test_string_paths_accepted(self, tmp_path):
        assert is_within(str(tmp_path), str(tmp_path / "x")) is True

    def test_non_existent_candidate_allowed_lexically(self, tmp_path):
        root = tmp_path / "pond"
        candidate = root / "video" / "not-here-yet.mp4"
        assert is_within(root, candidate) is True

    def test_symlink_escape_rejected(self, tmp_path):
        root = tmp_path / "pond"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("s")
        link = root / "evil"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform/filesystem")
        assert is_within(root, link / "secret.txt") is False

    def test_symlink_inside_root_allowed(self, tmp_path):
        root = tmp_path / "pond"
        (root / "real").mkdir(parents=True)
        link = root / "alias"
        try:
            link.symlink_to(root / "real", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform/filesystem")
        assert is_within(root, link / "f.mp4") is True


class TestBuildPath:
    def test_builds_path_inside_root(self, tmp_path):
        root = tmp_path / "pond"
        result = build_path(root, "video", "2026", "08")
        assert result == root / "video" / "2026" / "08"
        assert is_within(root, result)

    def test_rejects_parent_escape(self, tmp_path):
        root = tmp_path / "pond"
        with pytest.raises(PathOutsideRootError):
            build_path(root, "video", "..", "..", "etc")

    def test_rejects_absolute_component(self, tmp_path):
        root = tmp_path / "pond"
        with pytest.raises(PathOutsideRootError):
            build_path(root, "video", str(tmp_path / "elsewhere"))

    def test_internal_parent_components_normalized(self, tmp_path):
        root = tmp_path / "pond"
        result = build_path(root, "video", "..", "video", "2026")
        assert result == root / "video" / "2026"
        assert ".." not in result.parts

    def test_empty_components_return_root(self, tmp_path):
        root = tmp_path / "pond"
        assert build_path(root) == root
