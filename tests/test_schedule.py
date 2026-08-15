from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hermes.jobs import scheduled_time

CHICAGO = ZoneInfo("America/Chicago")


@pytest.mark.parametrize("hour", [11, 13, 15, 17, 19])
def test_apply_windows(hour):
    now = datetime(2026, 8, 14, hour, 2, tzinfo=CHICAGO)
    assert scheduled_time("harvest_apply", now).minute == 0


def test_apply_rejected_outside_window():
    with pytest.raises(RuntimeError):
        scheduled_time("harvest_apply", datetime(2026, 8, 14, 20, tzinfo=CHICAGO))


def test_gmail_only_at_eight_pm():
    assert scheduled_time(
        "gmail", datetime(2026, 8, 14, 20, 0, tzinfo=CHICAGO)
    ).hour == 20
    with pytest.raises(RuntimeError):
        scheduled_time("gmail", datetime(2026, 8, 14, 19, tzinfo=CHICAGO))


def test_dst_timezone_is_preserved():
    winter = scheduled_time(
        "harvest_apply", datetime(2027, 1, 15, 11, tzinfo=CHICAGO)
    )
    summer = scheduled_time(
        "harvest_apply", datetime(2027, 7, 15, 11, tzinfo=CHICAGO)
    )
    assert winter.utcoffset() != summer.utcoffset()
