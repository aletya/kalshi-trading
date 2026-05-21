"""Fair-value engine: GEFS ensemble slice -> probability per Kalshi bucket.

Pipeline (see PLAN.md):
  1. per-member daily highs            (src.model.daily_high)
  2. per-station/per-season bias fix   (src.model.bias)
  3. fit a Gaussian to the 31 corrected members, integrate it over each bucket

The Gaussian fit is the Phase 3 distribution choice: robust with ~31 members,
smooth tails. Whether it (and the ensemble spread it rests on) is well
calibrated is measured in Phase 6.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from src.common.config import Station
from src.model import bias
from src.model.daily_high import member_daily_highs

# A fair value needs enough members to fit a meaningful Gaussian; a run that
# only partially covers the target day yields fewer qualifying members.
DEFAULT_MIN_MEMBERS = 10


@dataclass(frozen=True)
class Bucket:
    """A half-open temperature interval ``[low, high)`` in °F.

    ``low=None`` means open below (-inf), ``high=None`` open above (+inf).
    Callers translate Kalshi market strikes into these continuous edges
    (applying a continuity correction, since the observed high is integer °F).
    """

    low: float | None
    high: float | None
    label: str = ""


@dataclass
class FairValueResult:
    """Fair value for one station/date: bucket probabilities + diagnostics."""

    station_id: str
    target_date: dt.date
    ok: bool
    n_members: int
    mu: float | None
    sigma: float | None
    raw_highs: list[float] = field(default_factory=list)
    corrected_highs: list[float] = field(default_factory=list)
    probabilities: dict[Bucket, float] = field(default_factory=dict)


def normal_cdf(x: float, mu: float, sigma: float) -> float:
    """P(X <= x) for X ~ N(mu, sigma). A zero-sigma fit is a point mass at mu."""
    if sigma <= 0.0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def fit_gaussian(values: list[float]) -> tuple[float, float]:
    """Fit N(mu, sigma) to ``values`` (sample std, ddof=1)."""
    if not values:
        raise ValueError("cannot fit a Gaussian to zero values")
    arr = np.asarray(values, dtype=float)
    mu = float(arr.mean())
    sigma = float(arr.std(ddof=1)) if arr.size >= 2 else 0.0
    return mu, sigma


def bucket_probability(mu: float, sigma: float, bucket: Bucket) -> float:
    """P(daily high falls in ``bucket``) under N(mu, sigma)."""
    lo = normal_cdf(bucket.low, mu, sigma) if bucket.low is not None else 0.0
    hi = normal_cdf(bucket.high, mu, sigma) if bucket.high is not None else 1.0
    return max(0.0, hi - lo)


def bucket_probabilities(
    mu: float, sigma: float, buckets: list[Bucket]
) -> dict[Bucket, float]:
    """Probability for each bucket under N(mu, sigma)."""
    return {b: bucket_probability(mu, sigma, b) for b in buckets}


def fair_value(
    ensemble_slice: pl.DataFrame,
    station: Station,
    target_date: dt.date,
    buckets: list[Bucket],
    bias_model: dict | None = None,
    min_members: int = DEFAULT_MIN_MEMBERS,
) -> FairValueResult:
    """Compute bucket probabilities for a station's daily high on ``target_date``.

    With ``bias_model=None`` the raw ensemble is used directly (handy for tests
    and for comparing pre/post bias correction).
    """
    raw = sorted(member_daily_highs(ensemble_slice, station, target_date).values())
    if len(raw) < min_members:
        return FairValueResult(
            station_id=station.id,
            target_date=target_date,
            ok=False,
            n_members=len(raw),
            mu=None,
            sigma=None,
            raw_highs=raw,
        )

    if bias_model is not None:
        corrected = bias.apply_bias(bias_model, station.id, target_date, raw)
    else:
        corrected = list(raw)

    mu, sigma = fit_gaussian(corrected)
    return FairValueResult(
        station_id=station.id,
        target_date=target_date,
        ok=True,
        n_members=len(raw),
        mu=mu,
        sigma=sigma,
        raw_highs=raw,
        corrected_highs=corrected,
        probabilities=bucket_probabilities(mu, sigma, buckets),
    )
