"""Tests for the parse application service (Task 8): batch validation,
parser selection, persistence of ParseTask rows, and partial-failure handling.

Covers the use case that ``POST /api/parse`` delegates to: URL-batch
validation through the domain :class:`~app.domain.ParseCommand` (empty list /
blank entries -> 1001, malformed URLs -> 1002, batch over the 50-URL limit ->
generic 400), per-URL parsing with failures collected into a ``failed`` list
while successes are persisted as ``ParseTask`` rows (enriched metadata with the
option ladders and file size), and the task_id round-trip (fresh UUIDs per
parse, persisted unchanged).
"""

import pytest
from sqlalchemy import select

from app.adapters.parser_stub import StubParserAdapter
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_URL_EMPTY,
    CODE_URL_INVALID,
    ApiError,
)
from app.application.parse_service import ParseService
from app.domain import MediaType, parse_duration
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import ParseTask

VIDEO_URL = "https://www.bilibili.com/video/av123"
MUSIC_URL = "https://music.example.com/song/hello.mp3"


class _FlakyParser:
    """A parser that fails for one specific URL, delegating the rest to the stub."""

    def __init__(self, bad_url: str) -> None:
        self._bad_url = bad_url
        self._delegate = StubParserAdapter()

    def parse(self, command):
        if command.urls[0] == self._bad_url:
            raise ValueError("platform engine unavailable")
        return self._delegate.parse(command)


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'parse-service.db'}")
    Base.metadata.create_all(engine)
    return engine


def rows(engine) -> list[ParseTask]:
    with session_scope(engine) as session:
        return list(session.scalars(select(ParseTask)))


class TestParseValidation:
    def test_empty_url_list_raises_1001(self, engine):
        with pytest.raises(ApiError) as excinfo:
            ParseService(engine=engine).parse([])
        assert excinfo.value.http_status == 400
        assert excinfo.value.code == CODE_URL_EMPTY

    def test_blank_entry_raises_1001(self, engine):
        with pytest.raises(ApiError) as excinfo:
            ParseService(engine=engine).parse(["   "])
        assert excinfo.value.http_status == 400
        assert excinfo.value.code == CODE_URL_EMPTY

    def test_too_many_urls_raises_bad_request(self, engine):
        urls = [f"https://example.com/video/{i}" for i in range(51)]
        with pytest.raises(ApiError) as excinfo:
            ParseService(engine=engine).parse(urls)
        assert excinfo.value.http_status == 400
        assert excinfo.value.code == CODE_BAD_REQUEST

    def test_malformed_urls_raise_1002(self, engine):
        for url in ("not-a-url", "ftp://example.com/file", "http://", "https://exa mple.com"):
            with pytest.raises(ApiError) as excinfo:
                ParseService(engine=engine).parse([url])
            assert excinfo.value.http_status == 400, url
            assert excinfo.value.code == CODE_URL_INVALID, url

    def test_validation_failure_persists_nothing(self, engine):
        with pytest.raises(ApiError):
            ParseService(engine=engine).parse(["not-a-url"])
        assert rows(engine) == []


class TestParseSuccess:
    def test_parse_returns_results_in_order(self, engine):
        batch = ParseService(engine=engine).parse([VIDEO_URL, MUSIC_URL])
        assert len(batch.results) == 2
        assert batch.failed == []
        assert batch.results[0].url == VIDEO_URL
        assert batch.results[1].url == MUSIC_URL

    def test_parse_persists_one_row_per_result(self, engine):
        service = ParseService(engine=engine)
        batch = service.parse([VIDEO_URL, MUSIC_URL])

        stored = rows(engine)
        assert len(stored) == 2
        by_id = {row.task_id: row for row in stored}
        for result in batch.results:
            row = by_id[result.task_id]
            assert row.url == result.url
            assert row.platform == result.platform
            assert row.media_type is result.media_type
            assert row.title == result.title
            assert row.cover_url == result.cover
            assert row.duration == parse_duration(result.duration)
            assert row.format == result.format

    def test_parse_enriches_metadata_with_ladder_and_size(self, engine):
        service = ParseService(engine=engine)
        result = service.parse([VIDEO_URL]).results[0]
        row = rows(engine)[0]
        assert row.metadata_["file_size_mb"] == result.file_size_mb
        assert row.metadata_["available_qualities"] == result.available_qualities
        assert row.metadata_["available_bitrates"] == result.available_bitrates
        assert row.metadata_["stub"] is True  # original metadata preserved

    def test_parse_strips_surrounding_whitespace_from_urls(self, engine):
        service = ParseService(engine=engine)
        batch = service.parse([f"  {VIDEO_URL}  "])
        assert batch.results[0].url == VIDEO_URL
        assert rows(engine)[0].url == VIDEO_URL

    def test_parse_same_url_twice_yields_distinct_task_ids(self, engine):
        service = ParseService(engine=engine)
        first = service.parse([VIDEO_URL]).results[0]
        second = service.parse([VIDEO_URL]).results[0]
        assert first.task_id != second.task_id
        stored = rows(engine)
        assert len(stored) == 2
        assert len({row.task_id for row in stored}) == 2

    def test_image_url_is_parsed_and_persisted(self, engine):
        image_url = "https://www.xiaohongshu.com/photo/cover.jpg"
        batch = ParseService(engine=engine).parse([image_url])
        assert batch.results[0].media_type is MediaType.IMAGE
        assert rows(engine)[0].media_type is MediaType.IMAGE


class TestPartialFailure:
    def test_failed_url_is_collected_and_successes_persisted(self, engine):
        bad_url = "https://bad.example.com/video/x"
        service = ParseService(parser=_FlakyParser(bad_url), engine=engine)
        batch = service.parse([VIDEO_URL, bad_url, MUSIC_URL])

        assert [r.url for r in batch.results] == [VIDEO_URL, MUSIC_URL]
        assert len(batch.failed) == 1
        failure = batch.failed[0]
        assert failure.url == bad_url
        assert failure.error  # a human-readable message
        # Only the successful parses are persisted.
        stored = rows(engine)
        assert len(stored) == 2
        assert {row.url for row in stored} == {VIDEO_URL, MUSIC_URL}
