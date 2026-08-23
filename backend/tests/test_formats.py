"""Tests for the duration format helpers (``app/domain/formats.py``).

The PRD carries durations as ``"MM:SS"`` display strings (e.g. "03:20") while
the ORM stores integer seconds — these helpers are the conversion boundary so
Tasks 8/9 never hand-parse either form.
"""

import pytest

from app.domain.formats import format_duration, parse_duration


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == "00:00"

    def test_seconds_and_minutes(self):
        assert format_duration(200) == "03:20"

    def test_minutes_can_exceed_59(self):
        # Videos longer than an hour stay MM:SS per the PRD display form.
        assert format_duration(3661) == "61:01"

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            format_duration(-1)

    def test_non_int_rejected(self):
        with pytest.raises(TypeError):
            format_duration("200")
        with pytest.raises(TypeError):
            format_duration(200.5)


class TestParseDuration:
    def test_standard(self):
        assert parse_duration("03:20") == 200

    def test_minutes_without_padding(self):
        assert parse_duration("3:20") == 200

    def test_minutes_can_exceed_59(self):
        assert parse_duration("61:01") == 3661

    def test_seconds_must_be_two_digits(self):
        with pytest.raises(ValueError):
            parse_duration("3:2")

    def test_seconds_cannot_exceed_59(self):
        with pytest.raises(ValueError):
            parse_duration("03:99")

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            parse_duration("abc")
        with pytest.raises(ValueError):
            parse_duration("03:20:10")
        with pytest.raises(ValueError):
            parse_duration("")

    def test_non_str_rejected(self):
        with pytest.raises(TypeError):
            parse_duration(200)

    def test_round_trip(self):
        assert parse_duration(format_duration(200)) == 200
        assert format_duration(parse_duration("04:30")) == "04:30"
