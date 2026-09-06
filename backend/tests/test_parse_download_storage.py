"""Integration check for parse -> real HTTP download -> Bubble -> Pond."""

from pathlib import Path

import httpx
from sqlalchemy import select

from app.adapters.downloader_engine import EngineDownloaderAdapter
from app.adapters.factory import get_one_time_token_provider, get_storage
from app.adapters.protocols import ParserAdapter
from app.application.download_service import DownloadService
from app.application.nas_service import NasService
from app.application.parse_service import ParseService
from app.domain import (
    DownloadStatus,
    MediaManifest,
    MediaResource,
    MediaType,
    ParseCommand,
    ParseResult,
)
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask
from app.workers.worker import run_once
from tests.conftest import FakeHub, make_settings


MEDIA_URL = "https://cdn.example/media.mp4?sig=integration-test"
PAYLOAD = b"real-http-streamed-media"


class _Parser(ParserAdapter):
    def parse(self, command: ParseCommand) -> list[ParseResult]:
        manifest = MediaManifest(
            kind="video",
            videos=(MediaResource(url=MEDIA_URL, format="mp4"),),
        )
        return [
            ParseResult(
                task_id="22222222-2222-2222-2222-222222222222",
                url=command.urls[0],
                media_type=MediaType.VIDEO,
                platform="douyin",
                title="真实下载验证",
                cover=None,
                duration=None,
                file_size_mb=None,
                format="mp4",
                metadata={
                    "manifest": manifest,
                    "video_url": MEDIA_URL,
                },
            )
        ]


def test_parse_to_engine_download_writes_bubble_then_moves_to_pond(tmp_path):
    """Use the real streaming downloader and real storage adapter end-to-end."""
    data_root = tmp_path / "data"
    settings = make_settings(
        database_url=f"sqlite:///{data_root / 'db' / 'koifetch.db'}",
        video_storage_path=data_root / "pond" / "video",
        image_storage_path=data_root / "pond" / "image",
        music_storage_path=data_root / "pond" / "music",
        temp_video_path=data_root / "bubble" / "video",
        temp_image_path=data_root / "bubble" / "image",
        temp_music_path=data_root / "bubble" / "music",
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    storage = get_storage(settings)
    token_provider = get_one_time_token_provider(settings)

    parsed = ParseService(parser=_Parser(), engine=engine).parse(
        ["https://v.douyin.com/integration/"]
    )
    assert parsed.failed == []
    task_id = parsed.results[0].task_id

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).split("?", 1)[0] == "https://cdn.example/media.mp4"
        return httpx.Response(
            200,
            request=request,
            content=PAYLOAD,
            headers={"content-length": str(len(PAYLOAD))},
        )

    downloader = EngineDownloaderAdapter(
        transport=httpx.MockTransport(handler),
        chunk_size=4,
    )
    download_service = DownloadService(
        token_provider=token_provider,
        storage=storage,
        downloader=downloader,
        engine=engine,
    )
    download = download_service.submit(task_id, format="mp4")

    assert run_once(
        engine,
        downloader,
        storage,
        FakeHub(),
        token_provider=token_provider,
        download_service=download_service,
    ) == 1

    with session_scope(engine) as session:
        row = session.scalar(
            select(DownloadTask).where(
                DownloadTask.download_id == download.download_id
            )
        )
        assert row is not None
        assert row.status is DownloadStatus.COMPLETED
        bubble_path = row.bubble_path

    assert bubble_path is not None
    assert Path(bubble_path).parent == (data_root / "bubble" / "video").resolve()
    bubble_file = next((data_root / "bubble" / "video").glob("*.mp4"))
    assert bubble_file.read_bytes() == PAYLOAD

    result = NasService(storage=storage, engine=engine).save(
        download.download_id, "/integration"
    )

    pond_file = next((data_root / "pond" / "video" / "integration").glob("*.mp4"))
    assert result.nas_path == f"/integration/{pond_file.name}"
    assert pond_file.read_bytes() == PAYLOAD
    assert not bubble_file.exists()
