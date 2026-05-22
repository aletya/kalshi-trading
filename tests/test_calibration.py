"""Offline unit tests for src/backtest/calibration.py — synthetic, known-answer."""

import math

from src.backtest import calibration
from src.backtest.calibration import Prediction, ReliabilityBin


def _pred(prob, outcome, lead=1):
    return Prediction(lead_days=lead, predicted_prob=prob, outcome=outcome)


# --- brier_score ----------------------------------------------------------
def test_brier_perfect():
    assert calibration.brier_score([_pred(1.0, 1), _pred(0.0, 0)]) == 0.0


def test_brier_coin_flip():
    # Two 50/50 calls, one right one wrong -> 0.25.
    assert calibration.brier_score([_pred(0.5, 1), _pred(0.5, 0)]) == 0.25


def test_brier_empty_is_nan():
    assert math.isnan(calibration.brier_score([]))


# --- reliability_table ----------------------------------------------------
def test_reliability_table_bins_a_known_set():
    # 10 predictions of 0.75; 7 happened -> lands in the 0.7-0.8 bin.
    preds = [_pred(0.75, 1) for _ in range(7)] + [_pred(0.75, 0) for _ in range(3)]
    bins = calibration.reliability_table(preds, n_bins=10)
    assert len(bins) == 10
    bin78 = bins[7]
    assert bin78.lo == 0.7 and bin78.count == 10
    assert bin78.mean_predicted == 0.75
    assert bin78.observed_frequency == 0.7
    # Every other bin is empty.
    assert sum(b.count for b in bins) == 10


def test_reliability_table_empty_input():
    bins = calibration.reliability_table([], n_bins=10)
    assert len(bins) == 10
    assert all(b.count == 0 for b in bins)
    assert all(math.isnan(b.mean_predicted) for b in bins)


def test_reliability_table_prob_one_lands_in_top_bin():
    bins = calibration.reliability_table([_pred(1.0, 1)], n_bins=10)
    assert bins[-1].count == 1


# --- calibration_slope ----------------------------------------------------
def _bin(pred, obs, count=100):
    return ReliabilityBin(lo=0.0, hi=1.0, count=count,
                          mean_predicted=pred, observed_frequency=obs)


def test_calibration_slope_perfect_is_one():
    bins = [_bin(0.2, 0.2), _bin(0.5, 0.5), _bin(0.8, 0.8)]
    assert calibration.calibration_slope(bins) == 1.0


def test_calibration_slope_overconfident_below_one():
    # Predictions spread 0.1-0.9 but outcomes pulled toward 0.5 -> slope < 1.
    bins = [_bin(0.1, 0.3), _bin(0.5, 0.5), _bin(0.9, 0.7)]
    slope = calibration.calibration_slope(bins)
    assert slope is not None and slope < 1.0


def test_calibration_slope_needs_two_bins():
    assert calibration.calibration_slope([_bin(0.5, 0.5)]) is None


# --- brier_by_lead --------------------------------------------------------
def test_brier_by_lead_groups_by_lead():
    preds = [_pred(1.0, 1, lead=1), _pred(0.0, 1, lead=5), _pred(0.0, 0, lead=5)]
    by_lead = calibration.brier_by_lead(preds)
    assert by_lead[1] == (1, 0.0)
    assert by_lead[5][0] == 2
    assert by_lead[5][1] == 0.5  # one prediction of 0 was wrong
