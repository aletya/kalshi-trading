"""Station-level bias correction (a MOS-lite).

GEFS has systematic, station- and season-specific error in the daily high it
implies — partly real model bias, partly the slight low bias from sampling a
continuous daytime max at 3-hour steps. We correct it with a simple per-station,
per-season linear regression of observed CLI high on the raw ensemble-mean high:

    observed ≈ a + b · raw

fitted on historical (forecast, observed) pairs, then applied to every member.

A season with too few pairs falls back to that station's pooled all-season fit;
a station with almost no data falls back to the identity. ``n_pairs`` and
``source`` are recorded so thin fits are visible (e.g. New Orleans — see PLAN).
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

from src.common.config import Station
from src.model.daily_high import member_daily_highs

SEASONS = ("DJF", "MAM", "JJA", "SON")
DEFAULT_MIN_PAIRS = 10
# A 00Z run usefully covers the target local day plus the next 6 (7 in total).
TARGET_DAYS_PER_RUN = 7


def season_of(date: dt.date) -> str:
    """Meteorological season: DJF / MAM / JJA / SON."""
    return SEASONS[(date.month % 12) // 3]


def _ols(xy: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Ordinary least squares y ≈ a + b·x. Returns (a, b, rmse)."""
    x = np.array([p[0] for p in xy], dtype=float)
    y = np.array([p[1] for p in xy], dtype=float)
    b, a = np.polyfit(x, y, 1)
    rmse = float(np.sqrt(np.mean((y - (a + b * x)) ** 2)))
    return float(a), float(b), rmse


def _load_observations(path: Path) -> dict[tuple[str, dt.date], float]:
    """observed_highs.parquet -> {(station_id, date): observed_high_f}."""
    df = pl.read_parquet(path)
    return {
        (row["station_id"], row["date"]): row["observed_high_f"]
        for row in df.iter_rows(named=True)
    }


def build_training_pairs(
    ensemble_dir: Path, observations_path: Path, stations: list[Station]
) -> list[dict]:
    """Join backfilled GEFS runs with observations into (forecast, observed) pairs.

    For each historical run and each target day it covers, the raw forecast is
    the ensemble-mean daily high; it is paired with the observed CLI high.
    """
    observations = _load_observations(observations_path)
    pairs: list[dict] = []

    for parquet in sorted(ensemble_dir.glob("*/gefs_*.parquet")):
        run = pl.read_parquet(parquet)
        if run.is_empty():
            continue
        init_date = run["init_time"][0].date()
        for offset in range(TARGET_DAYS_PER_RUN):
            target = init_date + dt.timedelta(days=offset)
            for station in stations:
                observed = observations.get((station.id, target))
                if observed is None:
                    continue
                highs = member_daily_highs(run, station, target)
                if not highs:
                    continue
                pairs.append(
                    {
                        "station_id": station.id,
                        "target_date": target,
                        "season": season_of(target),
                        "raw_mean": sum(highs.values()) / len(highs),
                        "observed": float(observed),
                    }
                )
    return pairs


def fit_bias(pairs: list[dict], min_pairs: int = DEFAULT_MIN_PAIRS) -> dict:
    """Fit a per-station, per-season bias model from training pairs.

    Each (station, season) cell gets concrete (a, b): its own fit when it has
    >= ``min_pairs`` pairs, else the station's pooled fit, else the identity.
    """
    by_cell: dict[tuple[str, str], list] = defaultdict(list)
    by_station: dict[str, list] = defaultdict(list)
    for p in pairs:
        xy = (p["raw_mean"], p["observed"])
        by_cell[(p["station_id"], p["season"])].append(xy)
        by_station[p["station_id"]].append(xy)

    station_fit = {
        s: (_ols(xy) if len(xy) >= 2 else (0.0, 1.0, float("nan")))
        for s, xy in by_station.items()
    }

    fits: dict[str, dict] = {}
    for station in sorted(by_station):
        for season in SEASONS:
            xy = by_cell.get((station, season), [])
            if len(xy) >= min_pairs:
                a, b, rmse = _ols(xy)
                source = "season"
            elif len(by_station[station]) >= 2:
                a, b, rmse = station_fit[station]
                source = "station-pooled"
            else:
                a, b, rmse = 0.0, 1.0, float("nan")
                source = "identity"
            fits[f"{station}|{season}"] = {
                "a": a,
                "b": b,
                "n_pairs": len(xy),
                "rmse": rmse,
                "source": source,
            }

    return {
        "fits": fits,
        "min_pairs": min_pairs,
        "n_pairs_total": len(pairs),
        "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def apply_bias(
    model: dict, station_id: str, target_date: dt.date, raw_values: list[float]
) -> list[float]:
    """Apply the bias correction to raw daily-high values for a station/date."""
    fit = model["fits"].get(f"{station_id}|{season_of(target_date)}")
    if fit is None:
        return list(raw_values)  # unknown station/season -> identity
    return [fit["a"] + fit["b"] * v for v in raw_values]


def save_bias_model(model: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=2))


def load_bias_model(path: Path) -> dict:
    return json.loads(Path(path).read_text())
