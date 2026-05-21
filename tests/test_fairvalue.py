"""Offline unit tests for src/model/fairvalue.py — synthetic, known-answer."""

import datetime as dt

import polars as pl
import pytest

from src.common.config import Station
from src.model import fairvalue
from src.model.fairvalue import Bucket

UTC = dt.timezone.utc


def _station() -> Station:
    return Station(
        id="KNYC", name="Test", kalshi_city="Test",
        latitude=40.0, longitude=-74.0, timezone="America/New_York",
    )


# --- fit_gaussian ---------------------------------------------------------
def test_fit_gaussian_basic():
    mu, sigma = fairvalue.fit_gaussian([70.0, 72.0, 74.0])
    assert mu == pytest.approx(72.0)
    assert sigma == pytest.approx(2.0)  # sample std of {70,72,74}


def test_fit_gaussian_identical_values_has_zero_sigma():
    mu, sigma = fairvalue.fit_gaussian([70.0] * 8)
    assert mu == pytest.approx(70.0)
    assert sigma == 0.0


# --- normal_cdf -----------------------------------------------------------
def test_normal_cdf_at_mean_is_half():
    assert fairvalue.normal_cdf(75.0, 75.0, 5.0) == pytest.approx(0.5)


def test_normal_cdf_zero_sigma_is_a_step():
    assert fairvalue.normal_cdf(70.0, 70.0, 0.0) == 1.0
    assert fairvalue.normal_cdf(69.9, 70.0, 0.0) == 0.0


# --- bucket_probability ---------------------------------------------------
def test_bucket_probability_point_mass():
    # All members agree on 70 -> sigma 0 -> the bucket holding 70 gets ~1.
    mu, sigma = fairvalue.fit_gaussian([70.0] * 31)
    assert fairvalue.bucket_probability(mu, sigma, Bucket(69.5, 70.5)) == 1.0
    assert fairvalue.bucket_probability(mu, sigma, Bucket(70.5, 71.5)) == 0.0


def test_bucket_probability_matches_gaussian_halves():
    mu, sigma = 75.0, 5.0
    assert fairvalue.bucket_probability(mu, sigma, Bucket(None, 75.0)) == pytest.approx(0.5)
    assert fairvalue.bucket_probability(mu, sigma, Bucket(75.0, None)) == pytest.approx(0.5)


def test_bucket_probabilities_partition_sums_to_one():
    buckets = [Bucket(None, 70.0), Bucket(70.0, 80.0), Bucket(80.0, None)]
    probs = fairvalue.bucket_probabilities(75.0, 5.0, buckets)
    assert sum(probs.values()) == pytest.approx(1.0)


# --- fair_value (end-to-end on a synthetic ensemble) ----------------------
def _synthetic_slice(member_highs: dict[str, float]) -> pl.DataFrame:
    """Build an ensemble slice where each member's max over the day is known."""
    hours = [
        dt.datetime(2025, 7, 15, h, tzinfo=UTC) for h in (6, 9, 12, 15, 18, 21)
    ] + [dt.datetime(2025, 7, 16, 0, tzinfo=UTC), dt.datetime(2025, 7, 16, 3, tzinfo=UTC)]
    rows = []
    for member, high in member_highs.items():
        for i, t in enumerate(hours):
            # One step equals `high`, the rest are cooler.
            rows.append({
                "station_id": "KNYC", "member": member,
                "valid_time": t, "temp_2m_f": high if i == 3 else high - 15.0,
            })
    return pl.DataFrame(rows)


def test_fair_value_end_to_end():
    member_highs = {f"m{k:02d}": 70.0 + k for k in range(12)}  # 70..81
    df = _synthetic_slice(member_highs)
    buckets = [Bucket(None, 75.0, "lo"), Bucket(75.0, None, "hi")]
    result = fairvalue.fair_value(df, _station(), dt.date(2025, 7, 15), buckets)

    assert result.ok
    assert result.n_members == 12
    assert result.mu == pytest.approx(75.5)
    assert sum(result.probabilities.values()) == pytest.approx(1.0)


def test_fair_value_not_ok_with_too_few_members():
    df = _synthetic_slice({"m00": 70.0, "m01": 71.0})  # only 2 members
    result = fairvalue.fair_value(df, _station(), dt.date(2025, 7, 15), [])
    assert not result.ok
    assert result.n_members == 2
