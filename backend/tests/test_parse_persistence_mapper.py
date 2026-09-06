"""Tests for the parser-result to persistence-record boundary."""

import logging

from app.application.parse_persistence import (
    to_parse_task,
    to_persisted_parse_task,
)
from app.application.parse_service import ParseService
from app.domain import LivePhotoPair, MediaManifest, MediaResource, MediaType, ParseResult
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import ParseTask
from sqlalchemy import select


def _live_photo_result() -> ParseResult:
    manifest = MediaManifest(
        kind="live_photo",
        live_photos=(
            LivePhotoPair(
                image=MediaResource(
                    url="https://cdn.example/live.heic",
                    format="heic",
                ),
                motion=MediaResource(
                    url="https://cdn.example/live.mov",
                    format="mov",
                ),
            ),
        ),
    )
    return ParseResult(
        task_id="11111111-1111-1111-1111-111111111111",
        url="https://v.douyin.com/live/",
        media_type=MediaType.LIVE_PHOTO,
        platform="douyin",
        title="Live photo",
        cover="https://cdn.example/live.heic",
        duration="00:03",
        file_size_mb=1.5,
        format="heic",
        available_qualities=["original"],
        available_bitrates=[],
        metadata={
            "engine": "f2",
            "manifest": manifest,
            "author": {"name": "author"},
            "source_id": "work-1",
            "published_at": "2026-09-06",
        },
    )


def test_parse_result_maps_to_stable_persistence_record_and_orm_row():
    record = to_persisted_parse_task(_live_photo_result())

    assert record.media_type is MediaType.LIVE_PHOTO
    assert record.duration == 3
    assert record.metadata["manifest"]["kind"] == "live_photo"
    assert record.metadata["file_size_mb"] == 1.5
    assert record.metadata["available_qualities"] == ["original"]
    assert record.metadata["source_id"] == "work-1"
    assert record.metadata["published_at"] == "2026-09-06"

    row = to_parse_task(record)

    assert row.media_type is MediaType.LIVE_PHOTO
    assert row.duration == 3
    assert row.metadata_["manifest"]["live_photos"][0]["motion"]["format"] == "mov"


def test_mapper_logs_media_links_before_persistence_without_signed_query_params(
    caplog,
):
    caplog.set_level(logging.INFO, logger="app.application.parse_persistence")
    result = _live_photo_result().model_copy(
        update={
            "metadata": {
                **_live_photo_result().metadata,
                "background_music_urls": [
                    "https://music.example/audio.mp3?sig=secret"
                ],
            }
        }
    )

    record = to_persisted_parse_task(result)

    message = caplog.text
    assert "live_photo_image_links=['https://cdn.example/live.heic']" in message
    assert "live_photo_motion_links=['https://cdn.example/live.mov']" in message
    assert "background_music_links=['https://music.example/audio.mp3']" in message
    assert "sig=secret" not in message
    assert record.metadata["background_music_urls"] == [
        "https://music.example/audio.mp3?sig=secret"
    ]


def test_mapper_logs_video_and_image_links(caplog):
    caplog.set_level(logging.INFO, logger="app.application.parse_persistence")
    video_result = _live_photo_result().model_copy(
        update={
            "media_type": MediaType.VIDEO,
            "metadata": {
                "manifest": MediaManifest(
                    kind="video",
                    videos=(
                        MediaResource(
                            url="https://cdn.example/video.mp4?token=secret",
                            format="mp4",
                        ),
                    ),
                )
            },
        }
    )
    image_result = _live_photo_result().model_copy(
        update={
            "media_type": MediaType.IMAGE,
            "metadata": {
                "manifest": MediaManifest(
                    kind="image_album",
                    images=(
                        MediaResource(
                            url="https://cdn.example/image.jpg?token=secret",
                            format="jpg",
                        ),
                    ),
                )
            },
        }
    )

    to_persisted_parse_task(video_result)
    to_persisted_parse_task(image_result)

    message = caplog.text
    assert "video_links=['https://cdn.example/video.mp4']" in message
    assert "image_links=['https://cdn.example/image.jpg']" in message
    assert "token=secret" not in message


class _LivePhotoParser:
    def parse(self, command):
        return [_live_photo_result().model_copy(update={"url": command.urls[0]})]


class _DuplicateTaskParser:
    def parse(self, command):
        return [_live_photo_result().model_copy(update={"url": command.urls[0]})]


def test_parse_service_persists_live_photo_through_mapper(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'live-photo.db'}")
    Base.metadata.create_all(engine)
    url = "https://v.douyin.com/live/"

    batch = ParseService(parser=_LivePhotoParser(), engine=engine).parse([url])

    assert [result.url for result in batch.results] == [url]
    assert batch.failed == []
    with session_scope(engine) as session:
        row = session.scalar(select(ParseTask).where(ParseTask.url == url))
    assert row is not None
    assert row.media_type is MediaType.LIVE_PHOTO
    assert row.metadata_["manifest"]["kind"] == "live_photo"


def test_database_failure_for_one_url_does_not_rollback_other_urls(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'duplicate-task.db'}")
    Base.metadata.create_all(engine)
    urls = [
        "https://v.douyin.com/live/one/",
        "https://v.douyin.com/live/two/",
    ]

    batch = ParseService(parser=_DuplicateTaskParser(), engine=engine).parse(urls)

    assert len(batch.results) == 1
    assert len(batch.failed) == 1
    assert batch.failed[0].url == urls[1]
    with session_scope(engine) as session:
        rows = list(session.scalars(select(ParseTask)))
    assert len(rows) == 1
    assert rows[0].url == urls[0]
