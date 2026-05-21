"""Storage helpers: filesystem paths and Parquet I/O for extracted data slices.

Per the project brief, extracted slices live in Parquet partitioned by date;
raw GRIB is never kept.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl


def ensemble_run_path(ensemble_dir: Path, init_date: dt.date, cycle: str) -> Path:
    """Parquet path for one GEFS run, partitioned by init date.

    e.g. data/ensemble/20260521/gefs_20260521_00z.parquet
    """
    day = init_date.strftime("%Y%m%d")
    return ensemble_dir / day / f"gefs_{day}_{cycle}z.parquet"


def observations_path(observations_dir: Path) -> Path:
    """Parquet path for the combined observed-daily-high table (all stations)."""
    return observations_dir / "observed_highs.parquet"


def bias_model_path(data_dir: Path) -> Path:
    """JSON path for the fitted per-station/per-season bias model."""
    return data_dir / "model" / "bias.json"


def latest_ensemble_parquet(ensemble_dir: Path) -> Path | None:
    """Most recent GEFS run Parquet on disk, by (init date, cycle); None if empty."""
    files = sorted(ensemble_dir.glob("*/gefs_*.parquet"), key=lambda p: p.stem)
    return files[-1] if files else None


def write_parquet(rows: list[dict], path: Path) -> int:
    """Write rows (a list of dicts) to a Parquet file, creating parent dirs.

    Returns the number of rows written. Raises ValueError on empty input —
    we never want to silently produce an empty slice.
    """
    if not rows:
        raise ValueError(f"Refusing to write an empty Parquet file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(path)
    return frame.height
