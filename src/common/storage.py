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
