from datetime import datetime, timedelta, timezone

import pytest

from irlpls import isotime

U = timezone.utc


@pytest.mark.parametrize("s,want", [
    ("2026-11-06T10:00:00Z", datetime(2026, 11, 6, 10, tzinfo=U)),
    ("2026-11-06T10:00:00z", datetime(2026, 11, 6, 10, tzinfo=U)),
    ("2026-11-06T10:00:00.5Z", datetime(2026, 11, 6, 10, 0, 0, 500000, tzinfo=U)),
    ("2026-11-06T10:00:00.1234567891Z", datetime(2026, 11, 6, 10, 0, 0, 123456, tzinfo=U)),
    ("2026-11-06T10:00:00.12+01:00", datetime(2026, 11, 6, 10, 0, 0, 120000, tzinfo=timezone(timedelta(hours=1)))),
    ("20261106T100000Z", datetime(2026, 11, 6, 10, tzinfo=U)),
    ("20261106T100000.25+0530", datetime(2026, 11, 6, 10, 0, 0, 250000, tzinfo=timezone(timedelta(hours=5, minutes=30)))),
    ("2026-11-06 10:00:00-07:00", datetime(2026, 11, 6, 10, tzinfo=timezone(timedelta(hours=-7)))),
    ("2026-11-06T10:00:00", datetime(2026, 11, 6, 10)),
    ("2026-11-06", datetime(2026, 11, 6)),
    ("  2026-11-06T10:00Z ", datetime(2026, 11, 6, 10, tzinfo=U)),
])
def test_parses(s, want):
    got = isotime.parse(s)
    assert got == want and (got.tzinfo is None) == (want.tzinfo is None)


@pytest.mark.parametrize("s", [None, 5, b"2026-11-06", "", "garbage", "None", "2026-13-45T00:00:00Z", "2026-11-06T25:00:00Z", "2026-11-06T10:00:00.Z"])
def test_garbage_is_none(s):
    assert isotime.parse(s) is None
