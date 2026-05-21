"""CLI: one Kalshi order-book logging pass.

Snapshots every configured temperature market's order book into data/kalshi.db.
Designed to be run on a schedule (launchd/cron) every poll_interval_minutes;
each run does one pass and exits.

    python scripts/log_kalshi.py
    python scripts/log_kalshi.py --verbose
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import datetime as dt  # noqa: E402

from src.common.config import load_config  # noqa: E402
from src.ingest import kalshi  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="One Kalshi order-book log pass.")
    parser.add_argument("--verbose", action="store_true", help="Per-station output.")
    args = parser.parse_args()

    config = load_config()
    conn = kalshi.connect(config.paths.database)
    started = time.monotonic()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    try:
        result = kalshi.log_once(config, conn, verbose=args.verbose)
    finally:
        conn.close()

    elapsed = time.monotonic() - started
    print(
        f"{stamp}  logged {result.snapshots_inserted} snapshots "
        f"({result.markets_seen} markets) in {elapsed:.0f}s, "
        f"{len(result.errors)} errors"
    )
    for err in result.errors[:10]:
        print(f"  ! {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
