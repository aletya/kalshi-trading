"""CLI: backfill historical GEFS runs for the bias-correction training set.

Ingests one GEFS run every --step-days over [--start, --end], reusing the
Phase 1 ingest_run. Each run's 7-day forecast tiles the calendar. Runs already
on disk are skipped, so the backfill is resumable.

    python scripts/backfill_gefs.py                       # default: 1 year, weekly
    python scripts/backfill_gefs.py --start 2025-01-01 --end 2025-06-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common import storage  # noqa: E402
from src.common.config import load_config  # noqa: E402
from src.ingest import gefs  # noqa: E402


def _parse_args() -> argparse.Namespace:
    today = dt.datetime.now(dt.timezone.utc).date()
    # End 8 days back so every target day a run covers already has observations.
    default_end = today - dt.timedelta(days=8)
    default_start = default_end - dt.timedelta(days=364)
    p = argparse.ArgumentParser(description="Backfill historical GEFS runs.")
    p.add_argument("--start", default=default_start.isoformat(), help="YYYY-MM-DD.")
    p.add_argument("--end", default=default_end.isoformat(), help="YYYY-MM-DD.")
    p.add_argument("--step-days", type=int, default=7, help="Days between runs.")
    p.add_argument("--cycle", default="00", choices=gefs.GEFS_CYCLES)
    p.add_argument("--workers", type=int, default=8)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    config = load_config()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    dates: list[dt.date] = []
    d = start
    while d <= end:
        dates.append(d)
        d += dt.timedelta(days=args.step_days)

    stations = list(config.stations)
    forecast_hours = list(config.gefs.forecast_hours)
    members = config.gefs.members
    print(
        f"Backfilling {len(dates)} GEFS runs ({args.cycle}Z), "
        f"{start} -> {end} every {args.step_days}d."
    )

    started = time.monotonic()
    ingested = skipped = failed = 0
    for i, run_date in enumerate(dates, 1):
        out_path = storage.ensemble_run_path(
            config.paths.ensemble_dir, run_date, args.cycle
        )
        if out_path.exists():
            skipped += 1
            print(f"  [{i:3}/{len(dates)}] {run_date} {args.cycle}Z  skip (on disk)")
            continue
        result = gefs.ingest_run(
            date=run_date,
            cycle=args.cycle,
            stations=stations,
            forecast_hours=forecast_hours,
            members=members,
            out_path=out_path,
            workers=args.workers,
        )
        if result.ok:
            ingested += 1
            print(
                f"  [{i:3}/{len(dates)}] {run_date} {args.cycle}Z  "
                f"{result.rows} rows, {result.retrieved_files}/{result.expected_files} files"
            )
        else:
            failed += 1
            print(f"  [{i:3}/{len(dates)}] {run_date} {args.cycle}Z  FAILED (no data)")

    elapsed = time.monotonic() - started
    print("-" * 60)
    print(
        f"[OK] Backfill done in {elapsed/60:.1f} min: "
        f"{ingested} ingested, {skipped} skipped, {failed} failed."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
