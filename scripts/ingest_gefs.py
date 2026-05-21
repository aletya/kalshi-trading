"""CLI: ingest one GEFS run into a Parquet ensemble slice.

Examples:
    python scripts/ingest_gefs.py --latest
    python scripts/ingest_gefs.py --date 2026-05-20 --cycle 00
    python scripts/ingest_gefs.py --latest --stations KNYC,KMDW --forecast-hours 24,48

Use --stations / --forecast-hours / --members to run a small subset while
iterating; omit them to ingest the full run defined in config.yaml.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

# Allow `import src...` when run as a plain script from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from src.common import storage  # noqa: E402
from src.common.config import Station, load_config  # noqa: E402
from src.ingest import gefs  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest one GEFS run -> Parquet slice.")
    p.add_argument("--date", help="Run date, YYYY-MM-DD (UTC).")
    p.add_argument("--cycle", choices=gefs.GEFS_CYCLES, help="Model-run cycle.")
    p.add_argument(
        "--latest", action="store_true", help="Auto-pick the most recent posted run."
    )
    p.add_argument("--stations", help="Comma-separated station ids (default: all).")
    p.add_argument(
        "--forecast-hours", help="Comma-separated lead hours (default: from config)."
    )
    p.add_argument("--members", type=int, help="Override ensemble size (for testing).")
    p.add_argument("--workers", type=int, default=8, help="Concurrent downloads.")
    return p.parse_args()


def _select_stations(config, raw: str | None) -> list[Station]:
    if not raw:
        return list(config.stations)
    wanted = [s.strip() for s in raw.split(",") if s.strip()]
    selected = [config.station(sid) for sid in wanted]  # raises KeyError if unknown
    return selected


def _select_forecast_hours(config, raw: str | None) -> list[int]:
    if not raw:
        return list(config.gefs.forecast_hours)
    return [int(h) for h in raw.split(",") if h.strip()]


def main() -> int:
    args = _parse_args()
    config = load_config()

    # --- Resolve which run to ingest ------------------------------------
    if args.latest:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            run_date, cycle = gefs.find_latest_run(client)
        print(f"Latest posted run: {run_date:%Y-%m-%d} {cycle}Z")
    elif args.date and args.cycle:
        try:
            run_date = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"[FAIL] Bad --date {args.date!r}; expected YYYY-MM-DD.")
            return 1
        cycle = args.cycle
    else:
        print("[FAIL] Provide either --latest, or both --date and --cycle.")
        return 1

    stations = _select_stations(config, args.stations)
    forecast_hours = _select_forecast_hours(config, args.forecast_hours)
    members = args.members or config.gefs.members
    out_path = storage.ensemble_run_path(config.paths.ensemble_dir, run_date, cycle)

    n_files = len(stations) and members * len(forecast_hours)
    print(
        f"Ingesting {members} members x {len(forecast_hours)} lead times "
        f"= {n_files} GRIB messages for {len(stations)} stations."
    )
    print(f"Output: {out_path}")

    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 50 == 0 or done == total:
            print(f"  ... {done}/{total} member-files processed", flush=True)

    result = gefs.ingest_run(
        date=run_date,
        cycle=cycle,
        stations=stations,
        forecast_hours=forecast_hours,
        members=members,
        out_path=out_path,
        workers=args.workers,
        progress=progress,
    )
    elapsed = time.monotonic() - started

    print("-" * 60)
    if not result.ok:
        print(f"[FAIL] No data retrieved. Missing {len(result.missing)} files.")
        if result.missing:
            print("  First few:", ", ".join(result.missing[:5]))
        print("  The run may not be posted yet — try an earlier cycle or --latest.")
        return 1

    size_mb = result.path.stat().st_size / 1e6
    print(
        f"[OK] Wrote {result.rows} rows "
        f"({result.retrieved_files}/{result.expected_files} member-files) "
        f"in {elapsed:.0f}s."
    )
    print(f"     {result.path}  ({size_mb:.2f} MB)")
    if result.missing:
        print(
            f"[WARN] {len(result.missing)} member-files missing/failed "
            f"(partial run): {', '.join(result.missing[:5])}"
            f"{' ...' if len(result.missing) > 5 else ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
