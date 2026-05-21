"""Offline unit tests for src/model/daily_high.py — local-day windowing."""

import datetime as dt

import polars as pl

from src.common.config import Station
from src.model import daily_high

UTC = dt.timezone.utc


def _station(station_id="KNYC", timezone="America/New_York") -> Station:
    return Station(
        id=station_id,
        name="Test",
        kalshi_city="Test",
        latitude=40.0,
        longitude=-74.0,
        timezone=timezone,
    )


def _row(station_id, member, valid_time, temp_f):
    return {
        "station_id": station_id,
        "member": member,
        "valid_time": valid_time,
        "temp_2m_f": temp_f,
    }


# --- standard_utc_offset --------------------------------------------------
def test_standard_offset_eastern_is_minus_5():
    # New York standard time (EST) is UTC-5, regardless of DST.
    assert daily_high.standard_utc_offset("America/New_York") == dt.timedelta(hours=-5)


def test_standard_offset_arizona_is_minus_7():
    assert daily_high.standard_utc_offset("America/Phoenix") == dt.timedelta(hours=-7)


# --- local_day_utc_window -------------------------------------------------
def test_local_day_window_eastern():
    start, end = daily_high.local_day_utc_window(
        dt.date(2025, 7, 15), "America/New_York"
    )
    # NYC local midnight (standard time) == 05:00 UTC.
    assert start == dt.datetime(2025, 7, 15, 5, tzinfo=UTC)
    assert end == dt.datetime(2025, 7, 16, 5, tzinfo=UTC)


# --- member_daily_highs ---------------------------------------------------
def _full_nyc_day(member, temps):
    """8 three-hourly steps spanning the NYC local-standard day 2025-07-15."""
    hours = [
        dt.datetime(2025, 7, 15, h, tzinfo=UTC) for h in (6, 9, 12, 15, 18, 21)
    ] + [dt.datetime(2025, 7, 16, 0, tzinfo=UTC), dt.datetime(2025, 7, 16, 3, tzinfo=UTC)]
    return [_row("KNYC", member, t, temp) for t, temp in zip(hours, temps)]


def test_member_daily_high_is_the_max_over_the_day():
    rows = _full_nyc_day("gec00", [60, 65, 72, 88, 90, 85, 70, 62])
    df = pl.DataFrame(rows)
    highs = daily_high.member_daily_highs(df, _station(), dt.date(2025, 7, 15))
    assert highs == {"gec00": 90.0}


def test_member_with_insufficient_coverage_is_dropped():
    full = _full_nyc_day("gec00", [60, 65, 72, 88, 90, 85, 70, 62])
    # gep01 has only 5 of 8 steps -> below the 7-step minimum.
    partial = _full_nyc_day("gep01", [50, 51, 52, 53, 54, 55, 56, 57])[:5]
    df = pl.DataFrame(full + partial)
    highs = daily_high.member_daily_highs(df, _station(), dt.date(2025, 7, 15))
    assert set(highs) == {"gec00"}


def test_seven_of_eight_steps_is_enough():
    rows = _full_nyc_day("gec00", [50, 50, 50, 77, 50, 50, 50, 50])[:7]
    df = pl.DataFrame(rows)
    highs = daily_high.member_daily_highs(df, _station(), dt.date(2025, 7, 15))
    assert highs == {"gec00": 77.0}


def test_temps_outside_the_target_day_are_ignored():
    rows = _full_nyc_day("gec00", [60, 65, 72, 88, 90, 85, 70, 62])
    # A scorching value on the *previous* local day must not leak in.
    rows.append(_row("KNYC", "gec00", dt.datetime(2025, 7, 15, 3, tzinfo=UTC), 110.0))
    df = pl.DataFrame(rows)
    highs = daily_high.member_daily_highs(df, _station(), dt.date(2025, 7, 15))
    assert highs == {"gec00": 90.0}
