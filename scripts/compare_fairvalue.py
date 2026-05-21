"""CLI: Checkpoint-3 demo — fair value vs. live Kalshi temperature markets.

For a few cities, fetches the live Kalshi high-temp markets (read-only), turns
each market's strike into a bucket, computes our bias-corrected fair value from
the most recent GEFS run, and prints them side by side.

    python scripts/compare_fairvalue.py
    python scripts/compare_fairvalue.py --cities KNYC,KMDW
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
import polars as pl  # noqa: E402

from src.common import storage  # noqa: E402
from src.common.config import load_config  # noqa: E402
from src.model import bias  # noqa: E402
from src.model import fairvalue  # noqa: E402
from src.model.fairvalue import Bucket  # noqa: E402

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"

# config station id -> Kalshi high-temp series ticker
CITY_SERIES = {
    "KNYC": "KXHIGHNY",
    "KMDW": "KXHIGHCHI",
    "KMIA": "KXHIGHMIA",
    "KDEN": "KXHIGHDEN",
    "KPHX": "KXHIGHTPHX",
}
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def parse_ticker_date(ticker: str) -> dt.date | None:
    """KXHIGHNY-26MAY22-T70 -> date(2026, 5, 22)."""
    parts = ticker.split("-")
    if len(parts) < 2 or len(parts[1]) < 7:
        return None
    seg = parts[1]
    try:
        return dt.date(2000 + int(seg[:2]), MONTHS[seg[2:5]], int(seg[5:7]))
    except (KeyError, ValueError):
        return None


def market_bucket(market: dict) -> Bucket | None:
    """Translate a Kalshi market strike into a continuous bucket.

    The observed high is integer °F, so we continuity-correct by 0.5 °F:
    ">70" resolves Yes iff high >= 71, i.e. P(X > 70.5).
    """
    st = market.get("strike_type")
    floor, cap = market.get("floor_strike"), market.get("cap_strike")
    if st == "greater" and floor is not None:
        return Bucket(floor + 0.5, None, f">{floor}")
    if st == "less" and cap is not None:
        return Bucket(None, cap - 0.5, f"<{cap}")
    if st == "between" and floor is not None and cap is not None:
        return Bucket(floor - 0.5, cap + 0.5, f"{floor}-{cap}")
    return None


def fetch_orderbook(
    client: httpx.Client, ticker: str
) -> tuple[float | None, float | None]:
    """Best yes bid/ask (probability units, 0-1) from the live orderbook.

    Kalshi's market-list summary never populates yes_bid/yes_ask, so the real
    quotes come from the orderbook endpoint. The book has a yes side and a no
    side, each sorted ascending by price; the best yes ask is 1 - best no bid
    (selling YES == buying NO). Returns None for an empty side.
    """
    resp = client.get(f"{KALSHI_API}/markets/{ticker}/orderbook")
    resp.raise_for_status()
    book = resp.json().get("orderbook_fp") or {}
    yes = book.get("yes_dollars") or []
    no = book.get("no_dollars") or []
    yes_bid = float(yes[-1][0]) if yes else None
    yes_ask = (1.0 - float(no[-1][0])) if no else None
    return yes_bid, yes_ask


def fetch_markets(client: httpx.Client, series_ticker: str) -> list[dict]:
    """Fetch a series' markets, keeping only those currently open for trading."""
    resp = client.get(
        f"{KALSHI_API}/markets",
        params={"series_ticker": series_ticker, "limit": 200},
    )
    resp.raise_for_status()
    markets = resp.json().get("markets", [])
    return [m for m in markets if m.get("status") in ("active", "open")]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fair value vs. live Kalshi prices.")
    p.add_argument("--cities", help="Comma-separated station ids (default: 5 cities).")
    p.add_argument("--dates", type=int, default=2, help="Nearest N target dates/city.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    config = load_config()

    parquet = storage.latest_ensemble_parquet(config.paths.ensemble_dir)
    if parquet is None:
        print("[FAIL] No GEFS run on disk. Run ingest_gefs.py --latest first.")
        return 1
    ensemble = pl.read_parquet(parquet)
    print(f"GEFS run: {parquet.name}")

    bias_path = storage.bias_model_path(config.paths.data_dir)
    if bias_path.exists():
        bias_model = bias.load_bias_model(bias_path)
        print(f"Bias model: {bias_path.name}  (fair value is bias-corrected)")
    else:
        bias_model = None
        print("Bias model: NONE — showing RAW (uncorrected) fair value.")

    city_ids = (
        [c.strip() for c in args.cities.split(",")]
        if args.cities
        else list(CITY_SERIES)
    )

    with httpx.Client(timeout=30.0) as client:
        for city_id in city_ids:
            series = CITY_SERIES.get(city_id)
            if series is None:
                print(f"\n{city_id}: no Kalshi series mapped — skipping.")
                continue
            station = config.station(city_id)
            markets = fetch_markets(client, series)

            by_date: dict[dt.date, list[dict]] = defaultdict(list)
            for m in markets:
                d = parse_ticker_date(m["ticker"])
                if d is not None:
                    by_date[d].append(m)

            print(f"\n{'=' * 64}\n{station.name}  ({series})")
            for target in sorted(by_date)[: args.dates]:
                mkts = by_date[target]
                buckets, bucket_of = [], {}
                for m in mkts:
                    b = market_bucket(m)
                    if b is not None:
                        buckets.append(b)
                        bucket_of[m["ticker"]] = b
                result = fairvalue.fair_value(
                    ensemble, station, target, buckets, bias_model
                )
                if not result.ok:
                    print(f"  {target}: insufficient ensemble coverage "
                          f"({result.n_members} members) — skipped.")
                    continue
                print(
                    f"  {target}  predicted high "
                    f"{result.mu:.1f} +/- {result.sigma:.1f} degF   "
                    f"({result.n_members} members)"
                )
                print(
                    f"    {'MARKET':26} {'BUCKET':8} {'OURS':>6} "
                    f"{'BID':>6} {'ASK':>6} {'MID':>6} {'EDGE':>7}"
                )
                for m in sorted(mkts, key=lambda x: x["ticker"]):
                    b = bucket_of.get(m["ticker"])
                    if b is None:
                        continue
                    ours = result.probabilities[b]
                    try:
                        yes_bid, yes_ask = fetch_orderbook(client, m["ticker"])
                    except httpx.HTTPError:
                        yes_bid = yes_ask = None
                    time.sleep(0.08)  # gentle on Kalshi's rate limit
                    if yes_bid is not None and yes_ask is not None:
                        mid = (yes_bid + yes_ask) / 2.0
                        bid_s = f"{yes_bid:5.0%}"
                        ask_s = f"{yes_ask:5.0%}"
                        mid_s = f"{mid:5.0%}"
                        edge_s = f"{ours - mid:+6.0%}"
                    else:
                        bid_s = ask_s = mid_s = "    --"
                        edge_s = "     --"
                    print(
                        f"    {m['ticker']:26} {b.label:8} {ours:5.0%} "
                        f"{bid_s:>6} {ask_s:>6} {mid_s:>6} {edge_s:>7}"
                    )

    print(f"\n{'=' * 64}")
    print("BID/ASK are best yes quotes from the live Kalshi orderbook (the "
          "markets-list summary omits them). '--' means an empty book side. "
          "EDGE = our fair value minus market mid; the bid-ask spread is a real "
          "cost, modelled properly in Phase 5.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
