"""Offline unit tests for the pure logic in src/ingest/gefs.py.

No network and no cfgrib here — the live "cfgrib + ecCodes works" check is the
CLI run itself, reviewed at Checkpoint 1.
"""

import datetime as dt
from pathlib import Path

import pytest

from src.ingest import gefs

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# --- .idx parsing ---------------------------------------------------------
def test_parse_idx_reads_all_messages():
    text = (FIXTURE_DIR / "sample.idx").read_text()
    entries = gefs.parse_idx(text)
    assert len(entries) == 6
    assert entries[0].msg_num == 1
    assert entries[3].variable == "TMP"
    assert entries[3].level == "2 m above ground"
    assert entries[3].start_byte == 140987


def test_parse_idx_skips_blank_and_malformed_lines():
    text = "\n1:0:d=x:TMP:2 m above ground:fcst\n\nGARBAGE\n"
    entries = gefs.parse_idx(text)
    assert len(entries) == 1
    assert entries[0].variable == "TMP"


def test_find_temp_byterange_middle_message():
    entries = gefs.parse_idx((FIXTURE_DIR / "sample.idx").read_text())
    start, end = gefs.find_temp_byterange(entries)
    # Message 4 starts at 140987; ends one byte before message 5 (189445).
    assert start == 140987
    assert end == 189444


def test_find_temp_byterange_last_message_runs_to_eof():
    entries = [
        gefs.IdxEntry(1, 0, "HGT", "10 mb"),
        gefs.IdxEntry(2, 500, "TMP", "2 m above ground"),
    ]
    start, end = gefs.find_temp_byterange(entries)
    assert start == 500
    assert end is None


def test_find_temp_byterange_missing_raises():
    entries = [gefs.IdxEntry(1, 0, "HGT", "10 mb")]
    with pytest.raises(gefs.MessageNotFound):
        gefs.find_temp_byterange(entries)


# --- keys & members -------------------------------------------------------
def test_member_names_full_ensemble():
    names = gefs.member_names(31)
    assert len(names) == 31
    assert names[0] == "gec00"
    assert names[1] == "gep01"
    assert names[-1] == "gep30"


def test_member_names_rejects_zero():
    with pytest.raises(ValueError):
        gefs.member_names(0)


def test_grib_key_format():
    key = gefs.grib_key(dt.date(2026, 5, 20), "00", "gep07", 24)
    assert key == "gefs.20260520/00/atmos/pgrb2ap5/gep07.t00z.pgrb2a.0p50.f024"


def test_grib_key_pads_forecast_hour_to_three_digits():
    assert gefs.grib_key(dt.date(2026, 5, 20), "12", "gec00", 3).endswith("f003")
    assert gefs.grib_key(dt.date(2026, 5, 20), "12", "gec00", 168).endswith("f168")


# --- unit conversions -----------------------------------------------------
def test_to_grid_longitude_wraps_negative_into_0_360():
    # New York City ~ -73.97 -> 286.03 on the GEFS 0..360 grid.
    assert gefs.to_grid_longitude(-73.9693) == pytest.approx(286.0307)
    assert gefs.to_grid_longitude(-122.379) == pytest.approx(237.621)


def test_to_grid_longitude_leaves_eastern_lon_unchanged():
    assert gefs.to_grid_longitude(10.0) == pytest.approx(10.0)


def test_kelvin_to_fahrenheit_known_points():
    assert gefs.kelvin_to_fahrenheit(273.15) == pytest.approx(32.0)
    assert gefs.kelvin_to_fahrenheit(373.15) == pytest.approx(212.0)
    assert gefs.kelvin_to_fahrenheit(300.0) == pytest.approx(80.33, abs=0.01)


# --- latest-run candidate generation -------------------------------------
def test_candidate_runs_are_recent_first_and_six_hourly():
    now = dt.datetime(2026, 5, 21, 14, 30, tzinfo=dt.timezone.utc)
    runs = gefs._candidate_runs(now, 3)
    # 14:30 UTC -> most recent cycle is 12Z, then 06Z, then 00Z.
    assert runs == [
        (dt.date(2026, 5, 21), "12"),
        (dt.date(2026, 5, 21), "06"),
        (dt.date(2026, 5, 21), "00"),
    ]


def test_candidate_runs_cross_midnight():
    now = dt.datetime(2026, 5, 21, 3, 0, tzinfo=dt.timezone.utc)
    runs = gefs._candidate_runs(now, 2)
    # 03:00 UTC -> 00Z today, then 18Z the previous day.
    assert runs == [
        (dt.date(2026, 5, 21), "00"),
        (dt.date(2026, 5, 20), "18"),
    ]
