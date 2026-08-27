"""Tests for the adapter layer (Task 6): protocols, stub parser, stub
downloader, and the adapter factory.

The stub adapters are deterministic and offline: they exist so the complete
parse → download → store workflow can be exercised end-to-end without the
engine packages installed. Tests here pin down:

* the protocol contracts are importable and structurally satisfied by the
  stubs (``typing.Protocol`` + ``runtime_checkable``),
* the stub parser is deterministic per URL (task_id excluded) and never
  touches the network,
* the stub downloader writes deterministic bytes and fires ordered progress
  callbacks,
* the factory returns the configured stub for each adapter type, and that
  engine mode switches to the real adapters by settings.
"""

import re
from pathlib import Path

import pytest

from app.adapters.downloader_stub import StubDownloaderAdapter
from app.adapters.factory import (
    get_access_token_provider,
    get_downloader,
    get_one_time_token_provider,
    get_parser,
    get_storage,
)
from app.adapters.parser_stub import StubParserAdapter
from app.adapters.protocols import (
    AccessTokenProvider,
    DownloadRequest,
    DownloaderAdapter,
    OneTimeTokenProvider,
    ParserAdapter,
    StorageAdapter,
)
from app.adapters.storage_local import LocalStorageAdapter
from app.adapters.tokens_jwt import (
    JwtAccessTokenProvider,
    JwtOneTimeTokenProvider,
)
from app.domain import (
    DownloadCommand,
    DownloadProgress,
    DownloadResult,
    DownloadStatus,
    MediaType,
    ParseCommand,
    ParseResult,
)

TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
DOWNLOAD_ID = "33333333-3333-3333-3333-333333333333"
# HS256 test secret, ≥32 bytes (keeps the suite free of PyJWT's
# InsecureKeyLengthWarning).
TEST_SECRET = "test-secret-key-0123456789abcdef"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@pytest.fixture
def settings(tmp_path):
    from app.infrastructure.config import Settings

    return Settings(
        admin_password="pw",
        secret_key=TEST_SECRET,
        video_storage_path=tmp_path / "pond/video",
        image_storage_path=tmp_path / "pond/image",
        music_storage_path=tmp_path / "pond/music",
        temp_video_path=tmp_path / "bubble/video",
        temp_image_path=tmp_path / "bubble/image",
        temp_music_path=tmp_path / "bubble/music",
    )


def protocol_members(protocol: type) -> set[str]:
    """Names of a protocol's own members (Python 3.12-safe).

    ``typing.get_protocol_members`` only exists on 3.13+; on 3.12 the same
    information is on ``__protocol_attrs__`` (with a defensive fallback to
    scanning the class namespace).
    """
    attrs = getattr(protocol, "__protocol_attrs__", None)
    if attrs is not None:
        return set(attrs)
    return {name for name in vars(protocol) if not name.startswith("_")}


class TestProtocolContracts:
    """The protocol definitions are importable and structurally complete."""

    def test_parser_protocol_members(self):
        assert "parse" in protocol_members(ParserAdapter)

    def test_downloader_protocol_members(self):
        members = protocol_members(DownloaderAdapter)
        assert "download" in members

    def test_storage_protocol_members(self):
        members = protocol_members(StorageAdapter)
        for name in (
            "bubble_root",
            "pond_root",
            "resolve_bubble",
            "resolve_pond",
            "save_file",
            "save_bytes",
            "move_to_pond",
            "read_bytes",
            "exists",
            "delete",
            "list_files",
        ):
            assert name in members, f"StorageAdapter missing {name}"

    def test_token_provider_protocol_members(self):
        assert {"issue", "validate"} <= set(protocol_members(AccessTokenProvider))
        assert {"issue", "validate"} <= set(protocol_members(OneTimeTokenProvider))

    def test_stub_parser_satisfies_protocol(self):
        assert isinstance(StubParserAdapter(), ParserAdapter)

    def test_stub_downloader_satisfies_protocol(self):
        assert isinstance(StubDownloaderAdapter(), DownloaderAdapter)

    def test_storage_adapter_satisfies_protocol(self, tmp_path):
        adapter = LocalStorageAdapter(
            pond_video=tmp_path / "pond/video",
            pond_image=tmp_path / "pond/image",
            pond_music=tmp_path / "pond/music",
            bubble_video=tmp_path / "bubble/video",
            bubble_image=tmp_path / "bubble/image",
            bubble_music=tmp_path / "bubble/music",
        )
        assert isinstance(adapter, StorageAdapter)

    def test_token_providers_satisfy_protocols(self):
        assert isinstance(JwtAccessTokenProvider(TEST_SECRET), AccessTokenProvider)
        assert isinstance(JwtOneTimeTokenProvider(TEST_SECRET), OneTimeTokenProvider)


class TestStubParser:
    """Deterministic, offline parsing for arbitrary URLs."""

    def _parse(self, url: str) -> ParseResult:
        results = StubParserAdapter().parse(ParseCommand(urls=[url]))
        assert len(results) == 1
        return results[0]

    def test_one_result_per_url(self):
        urls = [
            "https://example.com/videos/a.mp4",
            "https://example.com/music/b.mp3",
            "https://example.com/images/c.jpg",
        ]
        results = StubParserAdapter().parse(ParseCommand(urls=urls))
        assert len(results) == 3
        assert [r.url for r in results] == urls

    def test_media_type_from_extension(self):
        assert self._parse("https://example.com/c/clip.mp4").media_type is MediaType.VIDEO
        assert self._parse("https://example.com/c/movie.mkv").media_type is MediaType.VIDEO
        assert self._parse("https://example.com/c/song.mp3").media_type is MediaType.MUSIC
        assert self._parse("https://example.com/c/track.flac").media_type is MediaType.MUSIC
        assert self._parse("https://example.com/c/pic.jpg").media_type is MediaType.IMAGE
        assert self._parse("https://example.com/c/art.png").media_type is MediaType.IMAGE

    def test_media_type_from_url_keywords(self):
        assert self._parse("https://example.com/watch/abc123").media_type is MediaType.VIDEO
        assert self._parse("https://example.com/music/album1").media_type is MediaType.MUSIC
        assert self._parse("https://example.com/photos/day1").media_type is MediaType.IMAGE

    def test_unknown_url_defaults_to_video(self):
        # Documented fallback: URLs with no extension or keyword hint default
        # to VIDEO (the primary PRD use case).
        assert self._parse("https://example.com/things/xyz").media_type is MediaType.VIDEO

    def test_platform_from_hostname(self):
        assert self._parse("https://www.bilibili.com/video/BV1xx").platform == "bilibili"
        assert self._parse("https://v.douyin.com/abc/").platform == "douyin"
        assert self._parse("https://example.com/x").platform == "example.com"

    def test_title_derived_from_url(self):
        result = self._parse("https://example.com/videos/My%20Demo%20Clip.mp4")
        assert result.title == "My Demo Clip"

    def test_title_cjk_preserved(self):
        result = self._parse("https://example.com/videos/晴天示例.mp4")
        assert result.title == "晴天示例"

    def test_title_falls_back_to_hostname(self):
        result = self._parse("https://example.com/videos/")
        assert result.title == "example.com"

    def test_deterministic_same_url_same_output(self):
        url = "https://example.com/videos/晴天示例.mp4"
        first = self._parse(url)
        second = self._parse(url)
        assert first.media_type is second.media_type
        assert first.title == second.title
        assert first.platform == second.platform
        assert first.duration == second.duration
        assert first.file_size_mb == second.file_size_mb
        assert first.format == second.format
        assert first.available_qualities == second.available_qualities
        assert first.available_bitrates == second.available_bitrates
        assert first.metadata == second.metadata

    def test_task_id_is_fresh_per_parse(self):
        # task_id is the persistence identity and must NOT be derived from the
        # URL: re-parsing the same URL creates a new task row.
        first = self._parse("https://example.com/videos/a.mp4")
        second = self._parse("https://example.com/videos/a.mp4")
        assert first.task_id != second.task_id
        assert _UUID_RE.fullmatch(first.task_id) is not None
        assert _UUID_RE.fullmatch(second.task_id) is not None

    def test_media_type_specific_options(self):
        video = self._parse("https://example.com/videos/clip.mp4")
        assert video.format == "mp4"
        assert video.available_qualities == ["1080p", "720p", "480p"]
        assert video.available_bitrates == []

        music = self._parse("https://example.com/music/song.mp3")
        assert music.format == "mp3"
        assert music.available_bitrates == ["320kbps", "FLAC"]
        assert music.available_qualities == []

        image = self._parse("https://example.com/photos/pic.jpg")
        assert image.format == "jpg"
        assert image.available_qualities == []
        assert image.available_bitrates == []

    def test_duration_and_size_are_plausible_and_stable(self):
        for url in (
            "https://example.com/videos/clip.mp4",
            "https://example.com/music/song.mp3",
            "https://example.com/photos/pic.jpg",
        ):
            result = self._parse(url)
            assert result.duration is not None
            assert re.fullmatch(r"\d{2}:\d{2}", result.duration)
            assert result.file_size_mb is not None and result.file_size_mb > 0
            assert result.duration == self._parse(url).duration  # stable

    def test_success_has_no_error_and_stub_metadata(self):
        result = self._parse("https://example.com/videos/clip.mp4")
        assert result.error is None
        assert result.cover is None  # stub never fabricates cover URLs
        assert result.metadata.get("stub") is True

    def test_never_hits_network(self, monkeypatch):
        def _deny(*args, **kwargs):
            raise AssertionError("network access attempted")

        import socket
        import urllib.request

        monkeypatch.setattr(socket.socket, "connect", _deny)
        monkeypatch.setattr(socket, "create_connection", _deny)
        monkeypatch.setattr(urllib.request, "urlopen", _deny)
        self._parse("https://example.com/videos/clip.mp4")


class TestStubDownloader:
    """Writes deterministic local test media with ordered progress."""

    def _request(
        self,
        target_path: Path,
        *,
        download_id: str = DOWNLOAD_ID,
        title: str | None = "Demo",
        media_type: MediaType | None = MediaType.VIDEO,
        progress_callback=None,
    ) -> DownloadRequest:
        return DownloadRequest(
            command=DownloadCommand(task_id=TASK_ID),
            download_id=download_id,
            target_path=target_path,
            title=title,
            media_type=media_type,
            progress_callback=progress_callback,
        )

    def test_writes_file_of_expected_size(self, tmp_path):
        target = tmp_path / "out" / "clip.bin"
        downloader = StubDownloaderAdapter(total_bytes=1024, chunk_size=256)
        result = downloader.download(self._request(target))
        assert target.exists()
        assert len(target.read_bytes()) == 1024
        assert result.status is DownloadStatus.COMPLETED
        assert result.total_bytes == 1024
        assert result.downloaded_bytes == 1024
        assert result.progress == 100.0
        assert result.task_id == TASK_ID
        assert result.download_id == DOWNLOAD_ID
        assert result.title == "Demo"
        assert result.media_type is MediaType.VIDEO

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "dir" / "clip.bin"
        downloader = StubDownloaderAdapter(total_bytes=128, chunk_size=64)
        downloader.download(self._request(target))
        assert target.exists()

    def test_bytes_are_deterministic_per_download_id(self, tmp_path):
        downloader = StubDownloaderAdapter(total_bytes=512, chunk_size=128)
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        downloader.download(self._request(a))
        downloader.download(self._request(b))
        assert a.read_bytes() == b.read_bytes()

    def test_different_download_id_yields_different_bytes(self, tmp_path):
        downloader = StubDownloaderAdapter(total_bytes=512, chunk_size=128)
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        downloader.download(
            self._request(a, download_id="11111111-1111-1111-1111-111111111111")
        )
        downloader.download(
            self._request(b, download_id="22222222-2222-2222-2222-222222222222")
        )
        assert a.read_bytes() != b.read_bytes()

    def test_progress_callbacks_fire_in_order(self, tmp_path):
        seen: list[DownloadProgress] = []
        target = tmp_path / "clip.bin"
        downloader = StubDownloaderAdapter(total_bytes=1000, chunk_size=100)
        downloader.download(self._request(target, progress_callback=seen.append))

        assert len(seen) == 10
        for i, snapshot in enumerate(seen, start=1):
            assert snapshot.download_id == DOWNLOAD_ID
            assert snapshot.status is DownloadStatus.DOWNLOADING
            assert snapshot.total_bytes == 1000
            assert snapshot.downloaded_bytes == i * 100
            assert snapshot.progress == pytest.approx(float(i) * 10.0)
            assert snapshot.speed is not None and snapshot.speed >= 0.0

    def test_final_callback_reports_complete_bytes(self, tmp_path):
        seen: list[DownloadProgress] = []
        target = tmp_path / "clip.bin"
        downloader = StubDownloaderAdapter(total_bytes=1000, chunk_size=100)
        downloader.download(self._request(target, progress_callback=seen.append))
        assert seen[-1].progress == pytest.approx(100.0)
        assert seen[-1].downloaded_bytes == 1000

    def test_last_chunk_smaller_than_chunk_size(self, tmp_path):
        seen: list[DownloadProgress] = []
        target = tmp_path / "clip.bin"
        downloader = StubDownloaderAdapter(total_bytes=1000, chunk_size=300)
        result = downloader.download(self._request(target, progress_callback=seen.append))
        # 4 chunks: 300, 300, 300, 100
        assert [p.downloaded_bytes for p in seen] == [300, 600, 900, 1000]
        assert result.total_bytes == 1000
        assert result.downloaded_bytes == 1000

    def test_result_speed_reported(self, tmp_path):
        downloader = StubDownloaderAdapter(total_bytes=1000, chunk_size=100)
        result = downloader.download(self._request(tmp_path / "clip.bin"))
        assert result.speed is not None and result.speed > 0

    def test_zero_byte_download_rejected(self):
        # A 0-byte download would contradict the documented contract ("the
        # final callback reports the complete byte count"): no chunk ever
        # runs, so no callback fires. Refuse it up front.
        with pytest.raises(ValueError):
            StubDownloaderAdapter(total_bytes=0)

    def test_speed_limit_maps_to_chunk_delay(self):
        # 100 bytes per chunk at 1 MB/s ⇒ 1e-4 s between progress callbacks.
        adapter = StubDownloaderAdapter(
            total_bytes=1000, chunk_size=100, speed_limit_mb_s=1.0
        )
        assert adapter.chunk_size == 100
        assert adapter.chunk_delay == pytest.approx(100 / 1_000_000)

    def test_speed_limit_zero_means_unlimited(self):
        adapter = StubDownloaderAdapter(total_bytes=1000, chunk_size=100)
        assert adapter.chunk_delay == 0.0

    def test_speed_limit_and_chunk_delay_are_mutually_exclusive(self):
        with pytest.raises(ValueError):
            StubDownloaderAdapter(
                total_bytes=1000,
                chunk_size=100,
                speed_limit_mb_s=1.0,
                chunk_delay=0.5,
            )

    def test_invalid_constructor_args_rejected(self):
        with pytest.raises(ValueError):
            StubDownloaderAdapter(total_bytes=-1)
        with pytest.raises(ValueError):
            StubDownloaderAdapter(total_bytes=0)
        with pytest.raises(ValueError):
            StubDownloaderAdapter(chunk_size=0)
        with pytest.raises(ValueError):
            StubDownloaderAdapter(speed_limit_mb_s=-1)


class TestAdapterFactory:
    """Adapter selection: factory returns the configured stub for each type."""

    def test_get_parser_returns_stub(self):
        assert isinstance(get_parser(), StubParserAdapter)
        assert isinstance(get_parser(), ParserAdapter)

    def test_get_downloader_returns_stub(self):
        assert isinstance(get_downloader(), StubDownloaderAdapter)
        assert isinstance(get_downloader(), DownloaderAdapter)

    def test_factory_wires_speed_limit_into_downloader(self, settings):
        # download_speed_limit (MB/s) must flow into the stub downloader's
        # per-chunk delay so the worker's progress speed is observable; 0
        # (default) means unlimited.
        throttled = settings.model_copy(update={"download_speed_limit": 2})
        adapter = get_downloader(throttled)
        assert isinstance(adapter, StubDownloaderAdapter)
        assert adapter.chunk_delay == pytest.approx(
            adapter.chunk_size / (2 * 1_000_000)
        )
        assert get_downloader(settings).chunk_delay == 0.0

    def test_get_storage_returns_local_adapter(self, settings):
        storage = get_storage(settings)
        assert isinstance(storage, LocalStorageAdapter)
        assert isinstance(storage, StorageAdapter)
        assert storage.pond_root(MediaType.VIDEO) == (
            settings.video_storage_path.resolve()
        )

    def test_get_token_providers(self):
        access = get_access_token_provider()
        one_time = get_one_time_token_provider()
        assert isinstance(access, JwtAccessTokenProvider)
        assert isinstance(access, AccessTokenProvider)
        assert isinstance(one_time, JwtOneTimeTokenProvider)
        assert isinstance(one_time, OneTimeTokenProvider)

    def test_storage_from_factory_is_usable(self, settings):
        storage = get_storage(settings)
        stored = storage.save_bytes(MediaType.VIDEO, "clip.mp4", b"payload")
        assert storage.read_bytes(stored) == b"payload"

    def test_relative_roots_resolve_against_cwd(self, tmp_path, monkeypatch):
        # Documented rule: relative storage roots resolve against the process
        # working directory at adapter-construction time.
        monkeypatch.chdir(tmp_path)
        storage = get_storage()
        root = storage.pond_root(MediaType.VIDEO)
        assert root.is_absolute()
        assert root == (tmp_path / "data" / "pond" / "video").resolve()


class TestEngineModeFactory:
    """Factory switches between stub and real engines by settings.

    Engine-mode tests need the engine packages and skip individually when they
    are missing; the stub-mode tests always run.
    """

    def test_get_parser_defaults_to_stub(self, settings):
        assert isinstance(get_parser(settings), StubParserAdapter)

    def test_get_parser_engine_mode_returns_engine_adapter(self, settings):
        pytest.importorskip("parse_video_py")
        from app.adapters.parser_engine import EngineParserAdapter

        engine_settings = settings.model_copy(update={"parser_engine": "engine"})
        adapter = get_parser(engine_settings)
        assert isinstance(adapter, EngineParserAdapter)
        assert isinstance(adapter, ParserAdapter)

    def test_engine_parser_wires_timeout_and_proxy(self, settings):
        pytest.importorskip("parse_video_py")
        from app.adapters.parser_engine import EngineParserAdapter

        engine_settings = settings.model_copy(
            update={
                "parser_engine": "engine",
                "engine_timeout_seconds": 7.5,
                "engine_proxy": "http://proxy.local:3128",
            }
        )
        adapter = get_parser(engine_settings)
        assert isinstance(adapter, EngineParserAdapter)
        assert adapter._timeout == pytest.approx(7.5)
        assert adapter._proxy == "http://proxy.local:3128"

    def test_engine_parser_forwards_cookie_provider_and_legacy_fallback(self, settings):
        pytest.importorskip("parse_video_py")
        from app.adapters.parser_engine import EngineParserAdapter

        engine_settings = settings.model_copy(
            update={
                "parser_engine": "engine",
                "parser_legacy_fallback": False,
            }
        )
        provider = object()
        parser = get_parser(engine_settings, cookie_provider=provider)
        assert isinstance(parser, EngineParserAdapter)
        assert parser._cookie_provider is provider
        assert parser._enable_legacy_fallback is False

    def test_get_downloader_engine_mode_returns_engine_adapter(self, settings):
        pytest.importorskip("musicdl")
        from app.adapters.downloader_engine import EngineDownloaderAdapter

        engine_settings = settings.model_copy(
            update={"downloader_engine": "engine"}
        )
        adapter = get_downloader(engine_settings)
        assert isinstance(adapter, EngineDownloaderAdapter)
        assert isinstance(adapter, DownloaderAdapter)

    def test_engine_adapter_wires_timeout_and_proxy(self, settings):
        pytest.importorskip("musicdl")
        from app.adapters.downloader_engine import EngineDownloaderAdapter

        engine_settings = settings.model_copy(
            update={
                "downloader_engine": "engine",
                "engine_timeout_seconds": 7.5,
                "engine_download_timeout_seconds": 9.5,
                "engine_proxy": "http://proxy.local:3128",
            }
        )
        adapter = get_downloader(engine_settings)
        assert isinstance(adapter, EngineDownloaderAdapter)
        assert adapter._download_timeout == pytest.approx(9.5)
        assert adapter._timeout == pytest.approx(7.5)
        assert adapter._proxy == "http://proxy.local:3128"
        assert adapter._music_sources == engine_settings.musicdl_sources
