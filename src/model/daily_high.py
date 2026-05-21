"""Convert a GEFS ensemble slice into per-member daily-high temperatures.

A Kalshi "daily high" is the maximum temperature over the CLI day: local
midnight-to-midnight *standard* time, year-round (Kalshi uses local standard
time even during Daylight Saving — see README). We therefore window each
member's 3-hourly temperature trace to that exact local-standard day and take
the max.

Shared by the bias fitter (to build training pairs) and the fair-value engine.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import polars as pl

from src.common.config import Station

# GEFS is 3-hourly, so a full local day contains 8 steps. Require nearly all of
# them — a member missing the afternoon block cannot give a trustworthy high.
DEFAULT_MIN_STEPS = 7


def standard_utc_offset(timezone: str) -> dt.timedelta:
    """The station's *standard-time* UTC offset (DST ignored, as Kalshi does).

    Probed in mid-January, when every US timezone is on standard time.
    """
    probe = dt.datetime(2025, 1, 15, 12, 0, tzinfo=ZoneInfo(timezone))
    offset = probe.utcoffset()
    assert offset is not None  # IANA zones always resolve an offset
    return offset


def local_day_utc_window(
    target_date: dt.date, timezone: str
) -> tuple[dt.datetime, dt.datetime]:
    """UTC [start, end) covering the CLI local-standard-time day ``target_date``."""
    offset = standard_utc_offset(timezone)
    start_local = dt.datetime.combine(target_date, dt.time(0, 0))
    start_utc = (start_local - offset).replace(tzinfo=dt.timezone.utc)
    return start_utc, start_utc + dt.timedelta(days=1)


def member_daily_highs(
    slice_df: pl.DataFrame,
    station: Station,
    target_date: dt.date,
    min_steps: int = DEFAULT_MIN_STEPS,
) -> dict[str, float]:
    """Per-member daily high (°F) for ``station`` on ``target_date``.

    Returns ``member -> high``. A member whose trace covers fewer than
    ``min_steps`` of the local day is omitted (insufficient coverage); if no
    member qualifies the result is empty.
    """
    start, end = local_day_utc_window(target_date, station.timezone)
    day = slice_df.filter(
        (pl.col("station_id") == station.id)
        & (pl.col("valid_time") >= start)
        & (pl.col("valid_time") < end)
    )
    highs: dict[str, float] = {}
    for key, group in day.group_by("member"):
        member = key[0] if isinstance(key, tuple) else key
        if group.height >= min_steps:
            highs[member] = float(group["temp_2m_f"].max())
    return highs
