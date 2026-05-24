"""CLI: one paper-trading cycle.

Refreshes inputs (latest GEFS run + recent observations), then runs one
decide-and-settle pass over the live Kalshi order books logged by the Phase 4
agent. Designed to be called by launchd every 6 hours.

    python scripts/paper_trade.py
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from src.backtest import calibration  # noqa: E402
from src.common import storage  # noqa: E402
from src.common.config import load_config  # noqa: E402
from src.ingest import gefs, kalshi, observations  # noqa: E402
from src.model import bias  # noqa: E402
from src.papertrade import runner  # noqa: E402


def _ensure_latest_gefs(config) -> str:
    with httpx.Client(timeout=30.0) as client:
        run_date, cycle = gefs.find_latest_run(client)
    out_path = storage.ensemble_run_path(
        config.paths.ensemble_dir, run_date, cycle
    )
    if out_path.exists():
        return f"GEFS run {run_date} {cycle}Z already on disk."
    result = gefs.ingest_run(
        date=run_date, cycle=cycle,
        stations=list(config.stations),
        forecast_hours=list(config.gefs.forecast_hours),
        members=config.gefs.members,
        out_path=out_path,
    )
    return (
        f"Ingested GEFS {run_date} {cycle}Z — {result.rows} rows, "
        f"{result.retrieved_files}/{result.expected_files} member-files."
    )


def _refresh_recent_observations(config, days_back: int = 10) -> str:
    end = dt.datetime.now(dt.timezone.utc).date()
    start = end - dt.timedelta(days=days_back)
    out_path = storage.observations_path(config.paths.observations_dir)
    result = observations.ingest_observations(
        stations=list(config.stations),
        start=start, end=end, out_path=out_path,
    )
    return f"Observations refreshed ({start} → {end}); table now {result.rows} rows."


def _settled_summary(conn) -> str:
    settled = conn.execute(
        "SELECT our_prob, outcome, pnl, pnl_mid FROM paper_trades "
        "WHERE status = 'settled'"
    ).fetchall()
    n = len(settled)
    if n == 0:
        return "Settled trades: 0 (none have resolved yet)."
    wins = sum(1 for _, _, p, _ in settled if p > 0)
    total_pnl = sum(p for _, _, p, _ in settled)
    total_pnl_mid = sum(m for _, _, _, m in settled)
    brier = sum((op - o) ** 2 for op, o, _, _ in settled) / n
    return (
        f"Settled trades: {n} | hit rate {wins}/{n} = {wins / n:.0%} | "
        f"total P&L {total_pnl:+.3f} (net of spread) "
        f"vs mid-mid {total_pnl_mid:+.3f} | live Brier {brier:.4f}"
    )


def main() -> int:
    started = time.monotonic()
    config = load_config()
    print(f"--- paper-trade cycle {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} ---")
    print(_ensure_latest_gefs(config))
    print(_refresh_recent_observations(config))

    bias_path = storage.bias_model_path(config.paths.data_dir)
    bias_model = bias.load_bias_model(bias_path) if bias_path.exists() else None
    if bias_model is None:
        print("[warn] no bias model on disk — paper trades will use the raw ensemble")

    observations_dict = calibration.load_observations(
        storage.observations_path(config.paths.observations_dir)
    )

    conn = kalshi.connect(config.paths.database)
    try:
        result = runner.paper_trade_once(config, conn, bias_model, observations_dict)
        open_n = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE status='open'"
        ).fetchone()[0]
    finally:
        conn.close()

    elapsed = time.monotonic() - started
    print(
        f"Cycle: +{result.opened} new trades, {result.settled} settled, "
        f"{open_n} open. {elapsed:.0f}s."
    )
    if result.skipped:
        items = sorted(result.skipped.items(), key=lambda kv: -kv[1])
        print("  not traded: " + ", ".join(f"{k}={v}" for k, v in items))
    conn = kalshi.connect(config.paths.database)
    try:
        print(_settled_summary(conn))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
