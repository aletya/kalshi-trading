"""CLI: backtest the strategy over the logged Kalshi order books.

Replays GEFS fair values against logged quotes, settles each trade against the
observed CLI high, and prints a per-trade + aggregate P&L report with the
bid-ask spread subtracted from every figure.

    python scripts/run_backtest.py
    python scripts/run_backtest.py --trades        # also list every trade
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest import engine  # noqa: E402
from src.common import storage  # noqa: E402
from src.common.config import load_config  # noqa: E402
from src.model import bias  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest over the logged order books.")
    parser.add_argument("--trades", action="store_true", help="List every trade.")
    args = parser.parse_args()

    config = load_config()
    bias_path = storage.bias_model_path(config.paths.data_dir)
    bias_model = bias.load_bias_model(bias_path) if bias_path.exists() else None

    result = engine.run_backtest(config, bias_model=bias_model)
    trades = result.trades

    print("=" * 68)
    print("BACKTEST — hold-to-resolution, costs included (execution at the quote)")
    print("=" * 68)
    print(f"Bias model: {'applied' if bias_model else 'NONE (raw fair value)'}")
    if result.skipped:
        skips = ", ".join(f"{k}={v}" for k, v in sorted(result.skipped.items()))
        print(f"Markets not traded: {skips}")

    if not trades:
        print("\nNo completed trades yet. The order-book logger is still")
        print("accumulating history; resolved logged markets will appear here")
        print("as their observed highs publish. Re-run as the logs fill in.")
        return 0

    if args.trades:
        print(f"\n{'MARKET':26} {'SIDE':4} {'OURP':>5} {'PAID':>6} "
              f"{'OUT':>4} {'PNL':>7} {'PNL_MID':>8}")
        for t in sorted(trades, key=lambda x: x.target_date):
            print(
                f"{t.ticker:26} {t.side:4} {t.our_prob:5.0%} {t.price_paid:6.2f} "
                f"{('YES' if t.outcome else 'NO'):>4} {t.pnl:+7.3f} {t.pnl_mid:+8.3f}"
            )

    n = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    total_pnl = sum(t.pnl for t in trades)
    total_mid = sum(t.pnl_mid for t in trades)
    spread_cost = sum(t.spread_cost for t in trades)

    print(f"\n{'-' * 68}")
    print(f"Trades:            {n}")
    print(f"Hit rate:          {wins}/{n} = {wins / n:.0%}")
    print(f"Total P&L:         {total_pnl:+.3f}  (per contract, net of spread)")
    print(f"Mean P&L/trade:    {total_pnl / n:+.4f}")
    print(f"Total P&L mid-mid: {total_mid:+.3f}  (fantasy: ignores the spread)")
    print(f"Spread paid:       {spread_cost:.3f}  (the cost the mid-mid figure hides)")

    by_city: dict[str, list] = defaultdict(list)
    for t in trades:
        by_city[t.station_id].append(t)
    print("\nBy city:")
    for city, ts in sorted(by_city.items()):
        pnl = sum(t.pnl for t in ts)
        print(f"  {city:6} {len(ts):3} trades   P&L {pnl:+.3f}")

    print(f"\n{'-' * 68}")
    if total_pnl <= 0:
        print("VERDICT: no edge after costs — total P&L is not positive.")
    else:
        print(f"VERDICT: positive P&L ({total_pnl:+.3f}) on {n} trades. Small "
              "sample — needs far more before it means anything; watch the "
              "calibration report (Phase 6) too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
