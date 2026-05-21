"""CLI: backfill observed daily high temperatures into a Parquet table.

Source is the NWS Daily Climate Report (CLI) via the IEM archive — the exact
source Kalshi settles temperature markets on.

Examples:
    python scripts/ingest_observations.py
    python scripts/ingest_observations.py --start 2015-01-01
    python scripts/ingest_observations.py --stations KNYC,KHOU --start 2024-01-01
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

from src.common import storage  # noqa: E402
from src.common.config import Station, load_config  # noqa: E402
from src.ingest import observations  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill observed daily highs -> Parquet.")
    p.add_argument("--stations", help="Comma-separated station ids (default: all).")
    p.add_argument("--start", default="2019-01-01", help="Start date YYYY-MM-DD.")
    p.add_argument("--end", help="End date YYYY-MM-DD (default: today, UTC).")
    return p.parse_args()


def _select_stations(config, raw: str | None) -> list[Station]:
    if not raw:
        return list(config.stations)
    return [config.station(s.strip()) for s in raw.split(",") if s.strip()]


def main() -> int:
    args = _parse_args()
    config = load_config()

    try:
        start = dt.date.fromisoformat(args.start)
        end = (
            dt.date.fromisoformat(args.end)
            if args.end
            else dt.datetime.now(dt.timezone.utc).date()
        )
    except ValueError as exc:
        print(f"[FAIL] Bad date: {exc}")
        return 1

    stations = _select_stations(config, args.stations)
    out_path = storage.observations_path(config.paths.observations_dir)

    print(
        f"Backfilling observed daily highs for {len(stations)} stations, "
        f"{start} -> {end}."
    )
    print(f"Source: NWS CLI via IEM. Output: {out_path}")

    started = time.monotonic()
    result = observations.ingest_observations(
        stations=stations, start=start, end=end, out_path=out_path
    )
    elapsed = time.monotonic() - started

    print("-" * 60)
    if not result.ok:
        print("[FAIL] No observations retrieved.")
        if result.stations_failed:
            print(f"  Failed stations: {', '.join(result.stations_failed)}")
        return 1

    for sid, (count, first, last) in sorted(result.per_station.items()):
        span = f"{first} .. {last}" if count else "(no data)"
        print(f"  {sid:6} {count:5} days   {span}")

    size_mb = result.path.stat().st_size / 1e6
    print("-" * 60)
    print(f"[OK] Wrote {result.rows} rows in {elapsed:.0f}s.")
    print(f"     {result.path}  ({size_mb:.2f} MB)")
    if result.stations_failed:
        print(f"[WARN] Failed stations: {', '.join(result.stations_failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
