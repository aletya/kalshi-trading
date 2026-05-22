"""Calibration harness: is the fair-value model calibrated?

A model is calibrated if, among all the times it says "30%", the event happens
~30% of the time. We build (predicted probability, realised outcome) pairs from
the backfilled GEFS runs and the observed CLI highs, bin them by predicted
probability, and compare each bin's mean prediction to its observed frequency.

Predictions are threshold exceedances: for a forecast with daily-high Gaussian
N(mu, sigma) we ask `P(high > T)` for thresholds spread around the mean, and
check it against `observed > T`. Calibrating the CDF this way calibrates the
Kalshi bucket probabilities too (a bucket is a difference of the CDF).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from src.common.config import Station
from src.model import bias as bias_mod
from src.model.daily_high import member_daily_highs
from src.model.fairvalue import DEFAULT_MIN_MEMBERS, fit_gaussian, normal_cdf

# Thresholds placed at these multiples of sigma around the forecast mean, so
# every prediction set spans the full 0-1 probability range.
THRESHOLD_SIGMAS = (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0)
TARGET_DAYS_PER_RUN = 7


@dataclass(frozen=True)
class Prediction:
    """One threshold prediction: P(high > T) vs. whether it happened."""

    lead_days: int
    predicted_prob: float
    outcome: int  # 1 if observed high > T, else 0


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    count: int
    mean_predicted: float  # nan if the bin is empty
    observed_frequency: float  # nan if the bin is empty


def load_observations(path: Path) -> dict[tuple[str, dt.date], float]:
    """observed_highs.parquet -> {(station_id, date): observed_high_f}."""
    df = pl.read_parquet(path)
    return {
        (r["station_id"], r["date"]): r["observed_high_f"]
        for r in df.iter_rows(named=True)
    }


def build_predictions(
    ensemble_dir: Path,
    observations: dict[tuple[str, dt.date], float],
    stations: list[Station],
    bias_model: dict | None = None,
) -> list[Prediction]:
    """Threshold predictions from every backfilled GEFS run with a known outcome."""
    predictions: list[Prediction] = []
    for parquet in sorted(ensemble_dir.glob("*/gefs_*.parquet")):
        run = pl.read_parquet(parquet)
        if run.is_empty():
            continue
        init_date = run["init_time"][0].date()
        for lead in range(TARGET_DAYS_PER_RUN):
            target = init_date + dt.timedelta(days=lead)
            for station in stations:
                observed = observations.get((station.id, target))
                if observed is None:
                    continue
                raw = sorted(member_daily_highs(run, station, target).values())
                if len(raw) < DEFAULT_MIN_MEMBERS:
                    continue
                values = (
                    raw
                    if bias_model is None
                    else bias_mod.apply_bias(bias_model, station.id, target, raw)
                )
                mu, sigma = fit_gaussian(values)
                if sigma <= 0:
                    continue
                seen: set[int] = set()
                for k in THRESHOLD_SIGMAS:
                    threshold = round(mu + k * sigma)
                    if threshold in seen:
                        continue
                    seen.add(threshold)
                    # observed high is integer °F; ">T" means >= T+1.
                    prob = 1.0 - normal_cdf(threshold + 0.5, mu, sigma)
                    predictions.append(
                        Prediction(lead, prob, int(observed > threshold))
                    )
    return predictions


def reliability_table(
    predictions: list[Prediction], n_bins: int = 10
) -> list[ReliabilityBin]:
    """Bin predictions by predicted probability; compare to observed frequency."""
    bins: list[ReliabilityBin] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        # The top bin is closed so a prediction of exactly 1.0 lands somewhere.
        in_bin = [
            p
            for p in predictions
            if lo <= p.predicted_prob < hi
            or (i == n_bins - 1 and p.predicted_prob == 1.0)
        ]
        if in_bin:
            mean_pred = sum(p.predicted_prob for p in in_bin) / len(in_bin)
            obs_freq = sum(p.outcome for p in in_bin) / len(in_bin)
        else:
            mean_pred = obs_freq = float("nan")
        bins.append(ReliabilityBin(lo, hi, len(in_bin), mean_pred, obs_freq))
    return bins


def brier_score(predictions: list[Prediction]) -> float:
    """Mean squared error of the probabilities (0 = perfect, lower is better)."""
    if not predictions:
        return float("nan")
    return sum(
        (p.predicted_prob - p.outcome) ** 2 for p in predictions
    ) / len(predictions)


def brier_by_lead(predictions: list[Prediction]) -> dict[int, tuple[int, float]]:
    """Brier score per lead time -> {lead_days: (count, brier)}."""
    out: dict[int, tuple[int, float]] = {}
    leads = sorted({p.lead_days for p in predictions})
    for lead in leads:
        subset = [p for p in predictions if p.lead_days == lead]
        out[lead] = (len(subset), brier_score(subset))
    return out


def calibration_slope(bins: list[ReliabilityBin]) -> float | None:
    """Count-weighted slope of observed frequency vs. mean predicted probability.

    ~1.0 = calibrated; < 1.0 = over-confident (predictions too extreme,
    ensemble under-dispersed); > 1.0 = under-confident.
    """
    pts = [(b.mean_predicted, b.observed_frequency, b.count) for b in bins if b.count]
    if len(pts) < 2:
        return None
    total = sum(c for _, _, c in pts)
    mean_x = sum(x * c for x, _, c in pts) / total
    mean_y = sum(y * c for _, y, c in pts) / total
    num = sum(c * (x - mean_x) * (y - mean_y) for x, y, c in pts)
    den = sum(c * (x - mean_x) ** 2 for x, _, c in pts)
    return num / den if den > 0 else None
