"""Offline unit tests for src/model/bias.py — synthetic, known-answer."""

import datetime as dt

import pytest

from src.model import bias


# --- season_of ------------------------------------------------------------
def test_season_of():
    assert bias.season_of(dt.date(2025, 1, 15)) == "DJF"
    assert bias.season_of(dt.date(2025, 12, 1)) == "DJF"
    assert bias.season_of(dt.date(2025, 4, 1)) == "MAM"
    assert bias.season_of(dt.date(2025, 7, 1)) == "JJA"
    assert bias.season_of(dt.date(2025, 10, 1)) == "SON"


# --- _ols -----------------------------------------------------------------
def test_ols_recovers_known_line():
    # y = 3 + 0.5x exactly -> recover a=3, b=0.5, rmse=0.
    xy = [(x, 3.0 + 0.5 * x) for x in range(10, 30)]
    a, b, rmse = bias._ols(xy)
    assert a == pytest.approx(3.0)
    assert b == pytest.approx(0.5)
    assert rmse == pytest.approx(0.0, abs=1e-9)


# --- fit_bias -------------------------------------------------------------
def _pairs(station, season, n, a, b, start_raw=60.0):
    return [
        {
            "station_id": station,
            "season": season,
            "raw_mean": start_raw + i,
            "observed": a + b * (start_raw + i),
        }
        for i in range(n)
    ]


def test_fit_bias_uses_own_season_fit_when_enough_pairs():
    pairs = _pairs("KA", "JJA", 15, a=4.0, b=1.0)
    model = bias.fit_bias(pairs, min_pairs=10)
    fit = model["fits"]["KA|JJA"]
    assert fit["source"] == "season"
    assert fit["n_pairs"] == 15
    assert fit["a"] == pytest.approx(4.0)
    assert fit["b"] == pytest.approx(1.0)


def test_fit_bias_falls_back_to_station_pool_for_thin_season():
    # JJA is rich (15 pairs); DJF is thin (3) -> DJF uses the pooled fit.
    pairs = _pairs("KA", "JJA", 15, a=4.0, b=1.0) + _pairs(
        "KA", "DJF", 3, a=4.0, b=1.0, start_raw=20.0
    )
    model = bias.fit_bias(pairs, min_pairs=10)
    djf = model["fits"]["KA|DJF"]
    assert djf["source"] == "station-pooled"
    assert djf["n_pairs"] == 3
    assert djf["a"] == pytest.approx(4.0)


def test_fit_bias_records_every_station_season_cell():
    model = bias.fit_bias(_pairs("KA", "JJA", 12, a=0.0, b=1.0), min_pairs=10)
    for season in bias.SEASONS:
        assert f"KA|{season}" in model["fits"]


# --- apply_bias -----------------------------------------------------------
def test_apply_bias_applies_the_seasonal_fit():
    model = bias.fit_bias(_pairs("KA", "JJA", 15, a=5.0, b=2.0), min_pairs=10)
    corrected = bias.apply_bias(model, "KA", dt.date(2025, 7, 20), [10.0, 20.0])
    assert corrected == pytest.approx([25.0, 45.0])  # 5 + 2*x


def test_apply_bias_unknown_station_is_identity():
    model = bias.fit_bias(_pairs("KA", "JJA", 12, a=5.0, b=2.0), min_pairs=10)
    assert bias.apply_bias(model, "KZZ", dt.date(2025, 7, 20), [10.0]) == [10.0]
