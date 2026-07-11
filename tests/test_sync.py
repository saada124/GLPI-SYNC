import pytest
from datetime import datetime, timezone
from sync import Syncer


class TestParseTimestamp:
    def test_iso_with_tz(self):
        dt = Syncer._parse_timestamp("2026-07-11T00:08:29+00:00")
        assert dt == datetime(2026, 7, 11, 0, 8, 29, tzinfo=timezone.utc)

    def test_iso_without_tz(self):
        dt = Syncer._parse_timestamp("2026-07-11T00:08:29")
        assert dt == datetime(2026, 7, 11, 0, 8, 29, tzinfo=timezone.utc)

    def test_sql_with_tz(self):
        dt = Syncer._parse_timestamp("2026-07-11 00:08:29+00:00")
        assert dt == datetime(2026, 7, 11, 0, 8, 29, tzinfo=timezone.utc)

    def test_sql_without_tz(self):
        dt = Syncer._parse_timestamp("2026-07-11 00:08:29")
        assert dt == datetime(2026, 7, 11, 0, 8, 29, tzinfo=timezone.utc)

    def test_date_only(self):
        dt = Syncer._parse_timestamp("2026-07-11")
        assert dt == datetime(2026, 7, 11, tzinfo=timezone.utc)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse timestamp"):
            Syncer._parse_timestamp("not-a-date")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            Syncer._parse_timestamp("")

    def test_trailing_whitespace(self):
        dt = Syncer._parse_timestamp("  2026-07-11T00:08:29  ")
        assert dt == datetime(2026, 7, 11, 0, 8, 29, tzinfo=timezone.utc)
