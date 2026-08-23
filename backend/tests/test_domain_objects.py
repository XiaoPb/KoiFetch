"""Tests for domain value objects (``app/domain/models.py``): the parse and
download command/result models.

Also locks in that the domain enums remain the shared vocabulary re-exported
from ``app.domain`` (the ORM and application layers import them from here).
Note: ``test_models.py`` covers the SQLAlchemy ORM models — this file is the
domain-side counterpart.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.domain import (
    DownloadCommand,
    DownloadProgress,
    DownloadResult,
    DownloadStatus,
    MediaType,
    ParseCommand,
    ParseResult,
)
from app.domain.enums import DownloadStatus as EnumDownloadStatus
from app.domain.enums import MediaType as EnumMediaType

TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
DOWNLOAD_ID = "33333333-3333-3333-3333-333333333333"


class TestEnumsReused:
    def test_media_type_importable_from_domain(self):
        assert MediaType is EnumMediaType
        values = [MediaType.VIDEO.value, MediaType.IMAGE.value, MediaType.MUSIC.value]
        assert values == ["video", "image", "music"]

    def test_download_status_importable_from_domain(self):
        assert DownloadStatus is EnumDownloadStatus
        assert [s.value for s in DownloadStatus] == [
            "pending",
            "downloading",
            "completed",
            "failed",
            "expired",
        ]

    def test_str_enum_compares_with_plain_string(self):
        assert MediaType.VIDEO == "video"
        assert DownloadStatus.PENDING == "pending"


class TestParseCommand:
    def test_single_url(self):
        cmd = ParseCommand(urls=["https://example.com/video/123"])
        assert cmd.urls == ["https://example.com/video/123"]

    def test_multiple_urls_stripped(self):
        cmd = ParseCommand(urls=["  https://a.com/1  ", "https://b.com/2"])
        assert cmd.urls == ["https://a.com/1", "https://b.com/2"]

    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError):
            ParseCommand(urls=[])

    def test_missing_urls_rejected(self):
        with pytest.raises(ValidationError):
            ParseCommand()

    def test_invalid_scheme_rejected(self):
        with pytest.raises(ValidationError):
            ParseCommand(urls=["ftp://example.com/file"])

    def test_scheme_required(self):
        with pytest.raises(ValidationError):
            ParseCommand(urls=["example.com/video"])

    def test_empty_string_element_rejected(self):
        with pytest.raises(ValidationError):
            ParseCommand(urls=["https://a.com/1", "   "])

    def test_whitespace_inside_url_rejected(self):
        with pytest.raises(ValidationError):
            ParseCommand(urls=["https://exa mple.com/x"])

    def test_control_characters_rejected(self):
        with pytest.raises(ValidationError):
            ParseCommand(urls=["https://example.com/\x00"])

    def test_count_limit_enforced(self):
        urls = [f"https://a.com/{i}" for i in range(ParseCommand.MAX_URLS + 1)]
        with pytest.raises(ValidationError):
            ParseCommand(urls=urls)

    def test_at_limit_accepted(self):
        urls = [f"https://a.com/{i}" for i in range(ParseCommand.MAX_URLS)]
        assert len(ParseCommand(urls=urls).urls) == ParseCommand.MAX_URLS

    def test_round_trip(self):
        urls = ["https://example.com/video/123", "https://b.com/2"]
        cmd = ParseCommand(urls=urls)
        assert ParseCommand.model_validate(cmd.model_dump()).urls == urls


class TestParseResult:
    def _minimal(self, **kw):
        base = dict(
            task_id=TASK_ID,
            url="https://example.com/v/1",
            media_type=MediaType.VIDEO,
            platform="youtube",
            title="T",
        )
        base.update(kw)
        return ParseResult(**base)

    def test_builds_with_required_fields(self):
        r = self._minimal()
        assert r.task_id == TASK_ID
        assert r.media_type is MediaType.VIDEO
        assert r.platform == "youtube"
        assert r.title == "T"

    def test_media_type_accepts_string(self):
        r = self._minimal(media_type="image")
        assert r.media_type is MediaType.IMAGE

    def test_empty_platform_rejected(self):
        with pytest.raises(ValidationError):
            self._minimal(platform="   ")

    def test_empty_title_rejected(self):
        with pytest.raises(ValidationError):
            self._minimal(title="")

    def test_defaults(self):
        r = self._minimal()
        assert r.cover is None
        assert r.duration is None
        assert r.file_size_mb is None
        assert r.format is None
        assert r.available_qualities == []
        assert r.available_bitrates == []
        assert r.metadata == {}
        assert r.error is None

    def test_optional_fields(self):
        r = self._minimal(
            cover="https://c/x.jpg",
            duration="03:20",
            file_size_mb=156.7,
            format="mp4",
            available_qualities=["1080p", "720p"],
            available_bitrates=["FLAC"],
            metadata={"tags": ["a"]},
        )
        assert r.duration == "03:20"
        assert r.file_size_mb == 156.7
        assert r.available_qualities == ["1080p", "720p"]
        assert r.metadata == {"tags": ["a"]}

    def test_invalid_task_id_rejected(self):
        with pytest.raises(ValidationError):
            self._minimal(task_id="not-a-uuid")

    def test_task_id_accepts_uuid_object(self):
        r = self._minimal(task_id=uuid.UUID(TASK_ID))
        assert r.task_id == TASK_ID

    def test_task_id_uppercase_normalized(self):
        r = self._minimal(task_id=TASK_ID.upper())
        assert r.task_id == TASK_ID

    def test_round_trip_json(self):
        r = self._minimal(media_type=MediaType.MUSIC, duration="04:30")
        loaded = ParseResult.model_validate_json(r.model_dump_json())
        assert loaded == r
        assert loaded.media_type is MediaType.MUSIC

    def test_unknown_media_type_rejected(self):
        # live_photo is not in the v1 MediaType enum (documented limitation).
        with pytest.raises(ValidationError):
            self._minimal(media_type="live_photo")

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            self._minimal(unexpected="field")


class TestDownloadCommand:
    def test_minimal(self):
        cmd = DownloadCommand(task_id=TASK_ID)
        assert cmd.save_to_nas is False
        assert cmd.nas_path is None
        assert cmd.format is None
        assert cmd.quality is None
        assert cmd.bitrate is None

    def test_selections(self):
        cmd = DownloadCommand(
            task_id=TASK_ID, format="mp4", quality="1080p", bitrate="FLAC"
        )
        assert cmd.format == "mp4"
        assert cmd.quality == "1080p"
        assert cmd.bitrate == "FLAC"

    def test_save_to_nas_requires_path(self):
        with pytest.raises(ValidationError):
            DownloadCommand(task_id=TASK_ID, save_to_nas=True)

    def test_save_to_nas_with_path_ok(self):
        cmd = DownloadCommand(task_id=TASK_ID, save_to_nas=True, nas_path="/视频/抖音")
        assert cmd.nas_path == "/视频/抖音"

    def test_nas_path_stripped(self):
        cmd = DownloadCommand(task_id=TASK_ID, save_to_nas=True, nas_path="  /videos  ")
        assert cmd.nas_path == "/videos"

    def test_blank_nas_path_rejected(self):
        with pytest.raises(ValidationError):
            DownloadCommand(task_id=TASK_ID, save_to_nas=True, nas_path="   ")

    def test_invalid_task_id_rejected(self):
        with pytest.raises(ValidationError):
            DownloadCommand(task_id="nope")


class TestDownloadProgress:
    def test_defaults(self):
        p = DownloadProgress(
            download_id=DOWNLOAD_ID, status=DownloadStatus.DOWNLOADING
        )
        assert p.progress == 0.0
        assert p.speed is None
        assert p.downloaded_bytes is None
        assert p.total_bytes is None
        assert p.remaining_time is None
        assert p.error_message is None

    def test_full_progress(self):
        p = DownloadProgress(
            download_id=DOWNLOAD_ID,
            status=DownloadStatus.DOWNLOADING,
            progress=65.5,
            speed=2_400_000.0,
            downloaded_bytes=101_711_872,
            total_bytes=156_467_200,
            remaining_time=120.5,
        )
        assert p.progress == 65.5
        assert p.speed == 2_400_000.0
        assert p.remaining_time == 120.5

    def test_remaining_time_round_trip(self):
        p = DownloadProgress(
            download_id=DOWNLOAD_ID,
            status=DownloadStatus.DOWNLOADING,
            remaining_time=42.0,
        )
        loaded = DownloadProgress.model_validate_json(p.model_dump_json())
        assert loaded.remaining_time == 42.0

    def test_completed_requires_progress_100(self):
        with pytest.raises(ValidationError):
            DownloadProgress(
                download_id=DOWNLOAD_ID,
                status=DownloadStatus.COMPLETED,
                progress=99.9,
            )

    def test_progress_upper_bound(self):
        with pytest.raises(ValidationError):
            DownloadProgress(
                download_id=DOWNLOAD_ID,
                status=DownloadStatus.DOWNLOADING,
                progress=100.1,
            )

    def test_progress_lower_bound(self):
        with pytest.raises(ValidationError):
            DownloadProgress(
                download_id=DOWNLOAD_ID,
                status=DownloadStatus.DOWNLOADING,
                progress=-0.1,
            )

    def test_downloaded_cannot_exceed_total(self):
        with pytest.raises(ValidationError):
            DownloadProgress(
                download_id=DOWNLOAD_ID,
                status=DownloadStatus.DOWNLOADING,
                downloaded_bytes=200,
                total_bytes=100,
            )

    def test_completed_at_100_percent(self):
        p = DownloadProgress(
            download_id=DOWNLOAD_ID, status=DownloadStatus.COMPLETED, progress=100.0
        )
        assert p.status is DownloadStatus.COMPLETED

    def test_status_accepts_string(self):
        p = DownloadProgress(download_id=DOWNLOAD_ID, status="downloading")
        assert p.status is DownloadStatus.DOWNLOADING

    def test_invalid_download_id_rejected(self):
        with pytest.raises(ValidationError):
            DownloadProgress(download_id="nope", status=DownloadStatus.PENDING)


class TestDownloadResult:
    def test_defaults(self):
        r = DownloadResult(download_id=DOWNLOAD_ID, task_id=TASK_ID)
        assert r.status is DownloadStatus.PENDING
        assert r.progress == 0.0
        assert r.retry_count == 0
        assert r.title is None
        assert r.media_type is None
        assert r.bubble_path is None
        assert r.pond_path is None
        assert r.completed_at is None

    def test_full_snapshot(self):
        r = DownloadResult(
            download_id=DOWNLOAD_ID,
            task_id=TASK_ID,
            title="Video",
            media_type=MediaType.VIDEO,
            format="mp4",
            quality="1080p",
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            speed=1.0,
            total_bytes=1000,
            downloaded_bytes=1000,
            retry_count=2,
            bubble_path="/data/bubble/video/d/1.mp4",
        )
        assert r.status is DownloadStatus.COMPLETED
        assert r.media_type is MediaType.VIDEO
        assert r.retry_count == 2

    def test_negative_retry_rejected(self):
        with pytest.raises(ValidationError):
            DownloadResult(download_id=DOWNLOAD_ID, task_id=TASK_ID, retry_count=-1)

    def test_bytes_inconsistency_rejected(self):
        with pytest.raises(ValidationError):
            DownloadResult(
                download_id=DOWNLOAD_ID,
                task_id=TASK_ID,
                downloaded_bytes=200,
                total_bytes=100,
            )

    def test_completed_requires_progress_100(self):
        with pytest.raises(ValidationError):
            DownloadResult(
                download_id=DOWNLOAD_ID,
                task_id=TASK_ID,
                status=DownloadStatus.COMPLETED,
                progress=50.0,
            )

    def test_round_trip_json(self):
        r = DownloadResult(
            download_id=DOWNLOAD_ID, task_id=TASK_ID, status=DownloadStatus.FAILED
        )
        loaded = DownloadResult.model_validate_json(r.model_dump_json())
        assert loaded == r
        assert loaded.status is DownloadStatus.FAILED

    def test_deferred_fields_not_present(self):
        # Documented dispositions (module docstring): download_url is a
        # per-request construct built by the API layer; metadata_path and
        # sha256 are deferred to v1.1 (no Task 4 ORM columns). Pin their
        # absence so adding them is a deliberate contract change.
        assert "download_url" not in DownloadResult.model_fields
        assert "metadata_path" not in DownloadResult.model_fields
        assert "sha256" not in DownloadResult.model_fields
