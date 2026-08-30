"""TDD coverage for direct/staged transfer preparation (Task 2)."""

import uuid

import pytest

from app.api.responses import CODE_BAD_REQUEST, CODE_TASK_NOT_FOUND, ApiError
from app.application.transfer_service import TransferService
from app.domain import (
    AssetSelector,
    DownloadStatus,
    MediaManifest,
    MediaResource,
    MediaType,
    LivePhotoPair,
    StagedTransfer,
)
from app.infrastructure.database import session_scope
from app.infrastructure.models import DownloadTask, ParseTask


class RecordingDownloadService:
    def __init__(self, result_id="download-1"):
        self.calls = []
        self.result_id = result_id

    def submit(self, task_id, format=None, quality=None):
        self.calls.append((task_id, format, quality))
        return type(
            "DownloadResultStub",
            (),
            {
                "download_id": self.result_id,
                "status": DownloadStatus.PENDING,
            },
        )()


def resource(url="https://cdn.example/video.mp4?sig=private", fmt="mp4", quality="1080p"):
    return MediaResource(url=url, format=fmt, quality=quality)


def seed_manifest_task(engine, manifest, *, media_type=MediaType.VIDEO, title="A / title"):
    task_id = str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url="https://source.example/item",
                platform="test",
                media_type=media_type,
                title=title,
                format="mp4",
                metadata_={"manifest": manifest.model_dump(mode="json")},
            )
        )
    return task_id


def test_prepare_returns_same_origin_direct_url_without_upstream_url(engine):
    manifest = MediaManifest(
        kind="video",
        videos=(resource(), resource("https://cdn.example/video.webm", "webm", "720p")),
    )
    task_id = seed_manifest_task(engine, manifest)
    downloads = RecordingDownloadService()

    result = TransferService(download_service=downloads, engine=engine).prepare(
        task_id, AssetSelector(kind="video", index=1)
    )

    assert result.mode == "direct"
    assert result.url == f"/api/download/direct/{task_id}?kind=video&index=1"
    assert result.filename == "a-title.webm"
    assert "cdn.example" not in result.model_dump_json()
    assert downloads.calls == []


@pytest.mark.parametrize("url", [
    "https://cdn.example/PLAY.M3U8?token=secret",
    "http://cdn.example/path/manifest.MpD?x=1",
])
def test_prepare_stages_streaming_manifests(engine, url):
    manifest = MediaManifest(kind="video", videos=(resource(url, "mp4"),))
    task_id = seed_manifest_task(engine, manifest)
    downloads = RecordingDownloadService()

    result = TransferService(download_service=downloads, engine=engine).prepare(
        task_id, AssetSelector(kind="video")
    )

    assert isinstance(result, StagedTransfer)
    assert downloads.calls == [(task_id, "mp4", "1080p")]


def test_prepare_stages_package_once_without_claiming_package_format(engine):
    manifest = MediaManifest(
        kind="image_album",
        images=(resource("https://cdn.example/one.jpg", "jpg", None),),
    )
    task_id = seed_manifest_task(engine, manifest, media_type=MediaType.IMAGE)
    downloads = RecordingDownloadService()

    result = TransferService(download_service=downloads, engine=engine).prepare(
        task_id,
        AssetSelector(kind="image", package="album_zip"),
    )

    assert result.mode == "staged"
    assert len(downloads.calls) == 1
    assert downloads.calls[0][0] == task_id


def test_prepare_force_staged_does_not_create_direct_transfer(engine):
    manifest = MediaManifest(kind="video", videos=(resource(),))
    task_id = seed_manifest_task(engine, manifest)
    downloads = RecordingDownloadService()

    result = TransferService(download_service=downloads, engine=engine).prepare(
        task_id, AssetSelector(kind="video"), force_staged=True
    )

    assert result.mode == "staged"
    assert len(downloads.calls) == 1


def test_prepare_resolves_live_motion_and_rejects_missing_motion(engine):
    manifest = MediaManifest(
        kind="live_photo",
        live_photos=(
            LivePhotoPair(
                image=resource("https://cdn.example/still.heic", "heic", None),
                motion=None,
            ),
        ),
    )
    task_id = seed_manifest_task(engine, manifest, media_type=MediaType.LIVE_PHOTO)
    with pytest.raises(ApiError) as exc:
        TransferService(download_service=RecordingDownloadService(), engine=engine).prepare(
            task_id, AssetSelector(kind="live_motion")
        )
    assert exc.value.http_status == 400
    assert exc.value.code == CODE_BAD_REQUEST


@pytest.mark.parametrize("selector", [
    AssetSelector(kind="image"),
    AssetSelector(kind="video", index=2),
])
def test_prepare_rejects_kind_or_index_mismatch(engine, selector):
    task_id = seed_manifest_task(
        engine,
        MediaManifest(kind="video", videos=(resource(),)),
    )
    with pytest.raises(ApiError) as exc:
        TransferService(download_service=RecordingDownloadService(), engine=engine).prepare(
            task_id, selector
        )
    assert exc.value.code == CODE_BAD_REQUEST


def test_prepare_unknown_task_uses_existing_3001_contract(engine):
    with pytest.raises(ApiError) as exc:
        TransferService(download_service=RecordingDownloadService(), engine=engine).prepare(
            str(uuid.uuid4()), AssetSelector(kind="video")
        )
    assert exc.value.code == CODE_TASK_NOT_FOUND


def test_prepare_music_stages_without_returning_persisted_upstream_url(engine):
    task_id = str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url="musicdl://source/song",
                platform="music",
                media_type=MediaType.MUSIC,
                title="Track",
                format="mp3",
                metadata_={
                    "engine": "musicdl",
                    "song_info": {"download_url": "https://cdn.example/track.mp3"},
                },
            )
        )
    downloads = RecordingDownloadService()
    result = TransferService(download_service=downloads, engine=engine).prepare(
        task_id, AssetSelector(kind="music")
    )
    assert result.mode == "staged"
    assert "cdn.example" not in result.model_dump_json()
    assert downloads.calls == [(task_id, "mp3", None)]
