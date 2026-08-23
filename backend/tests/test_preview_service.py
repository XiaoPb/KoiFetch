"""Unit tests for the preview application service (Task 8): direct calls,
no HTTP layer.

Covers the use case that ``GET /api/preview/{task_id}`` delegates to: loading
a :class:`ParseTask` by id and building the v1 metadata-only preview dict
(preview_type from media_type, duration reformatted to MM:SS, qualities/
bitrates read back from the persisted metadata JSON, and the ``streams``
ladder), the 3001 任务不存在 failure for a well-formed but unknown task_id,
and the ``None``/``[]`` fallbacks for rows whose metadata lacks the enriched
keys (older rows or future engines that skip the parse-service enrichment).
"""

import uuid

import pytest

from app.api.responses import CODE_TASK_NOT_FOUND, ApiError
from app.application.preview_service import PreviewService
from app.domain import MediaType
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import ParseTask


def make_task_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'preview-service.db'}")
    Base.metadata.create_all(engine)
    return engine


def _seed_task(engine, *, task_id, url, media_type, format, title="t", duration=323,
               metadata=None) -> None:
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url=url,
                platform="bilibili" if media_type is MediaType.VIDEO else "example",
                media_type=media_type,
                title=title,
                cover_url="https://cdn.example.com/cover.jpg",
                duration=duration,
                format=format,
                metadata_=metadata or {},
            )
        )


class TestPreviewService:
    def test_preview_existing_task_returns_metadata_dict(self, engine):
        task_id = make_task_id()
        _seed_task(
            engine,
            task_id=task_id,
            url="https://www.bilibili.com/video/av123",
            media_type=MediaType.VIDEO,
            format="mp4",
            title="av123",
            metadata={
                "file_size_mb": 12.5,
                "available_qualities": ["1080p", "720p", "480p"],
                "available_bitrates": [],
            },
        )
        data = PreviewService(engine=engine).preview(task_id)
        assert data["task_id"] == task_id
        assert data["preview_type"] == "video"
        assert data["url"] == "https://www.bilibili.com/video/av123"
        assert data["platform"] == "bilibili"
        assert data["title"] == "av123"
        assert data["cover"] == "https://cdn.example.com/cover.jpg"
        assert data["duration"] == "05:23"  # 323s → MM:SS
        assert data["format"] == "mp4"
        assert data["file_size_mb"] == 12.5
        assert data["available_qualities"] == ["1080p", "720p", "480p"]
        assert data["streams"] == [
            {"quality": "1080p", "format": "mp4"},
            {"quality": "720p", "format": "mp4"},
            {"quality": "480p", "format": "mp4"},
        ]

    def test_preview_missing_task_raises_3001(self, engine):
        with pytest.raises(ApiError) as excinfo:
            PreviewService(engine=engine).preview(make_task_id())
        assert excinfo.value.http_status == 400
        assert excinfo.value.code == CODE_TASK_NOT_FOUND

    def test_preview_task_without_enriched_metadata_uses_fallbacks(self, engine):
        task_id = make_task_id()
        _seed_task(
            engine,
            task_id=task_id,
            url="https://music.example.com/song/hello.mp3",
            media_type=MediaType.MUSIC,
            format="mp3",
            title="hello",
            metadata={"stub": True},
        )
        data = PreviewService(engine=engine).preview(task_id)
        assert data["preview_type"] == "music"
        assert data["file_size_mb"] is None
        assert data["available_qualities"] == []
        assert data["available_bitrates"] == []
        assert data["streams"] == []

    def test_preview_image_task_has_no_streams(self, engine):
        task_id = make_task_id()
        _seed_task(
            engine,
            task_id=task_id,
            url="https://www.xiaohongshu.com/photo/cover.jpg",
            media_type=MediaType.IMAGE,
            format="jpg",
            title="cover",
        )
        data = PreviewService(engine=engine).preview(task_id)
        assert data["preview_type"] == "image"
        assert data["streams"] == []
