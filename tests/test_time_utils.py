from __future__ import annotations

from datetime import datetime

import pytest

from power_operator.time_utils import (
    format_float,
    format_simu_time,
    format_wall_time,
    parse_simu_time,
)


def test_float_display_uses_exactly_three_decimal_places():
    assert format_float(1) == "1.000"
    assert format_float(1.23456) == "1.235"


def test_simulation_time_formats_without_24_hour_wrap():
    assert format_simu_time(0) == "00:00:00"
    assert format_simu_time(3661) == "01:01:01"
    assert format_simu_time(25 * 3600 + 2) == "25:00:02"


def test_wall_time_formats_as_local_calendar_time():
    timestamp = 1_787_422_688
    assert format_wall_time(0) == "--"
    assert format_wall_time(timestamp) == datetime.fromtimestamp(timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_simulation_time_parser_accepts_seconds_or_hhmmss():
    assert parse_simu_time("3661") == 3661
    assert parse_simu_time("01:01:01") == 3661
    with pytest.raises(ValueError):
        parse_simu_time("01:61:00")
