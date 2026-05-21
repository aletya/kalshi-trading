"""Read-only helper: find each city's active Kalshi high-temp series.

A city can have several legacy series tickers; the active one is whichever has
tradeable markets right now. This reconciles that against config.yaml's
`kalshi_series` fields. Re-run if Kalshi renames a series.

    python scripts/discover_kalshi_series.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from src.common.config import load_config  # noqa: E402

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
_EXCLUDE = ("united states", "cities", "100", "arctic", "sea ice")


def main() -> int:
    config = load_config()
    print("Discovering active Kalshi high-temp series ...")

    with httpx.Client(timeout=30.0) as client:
        series = client.get(
            f"{KALSHI_API}/series", params={"category": "Climate and Weather"}
        ).json().get("series", [])

        # Group candidate series by CLI station code (issuedby).
        by_station: dict[str, list[str]] = {}
        for s in series:
            title = s.get("title", "").lower()
            if not ("high" in title or "max" in title):
                continue
            if any(word in title for word in _EXCLUDE):
                continue
            src = (s.get("settlement_sources") or [{}])[0]
            issuedby = parse_qs(urlparse(src.get("url", "")).query).get("issuedby")
            if issuedby:
                by_station.setdefault("K" + issuedby[0].upper(), []).append(s["ticker"])

        # For each station, the active series is the one with the most markets.
        active: dict[str, str] = {}
        for station_id, tickers in by_station.items():
            best, best_n = None, -1
            for ticker in tickers:
                markets = client.get(
                    f"{KALSHI_API}/markets",
                    params={"series_ticker": ticker, "limit": 200},
                ).json().get("markets", [])
                n = sum(1 for m in markets if m.get("status") in ("active", "open"))
                time.sleep(0.25)
                if n > best_n:
                    best, best_n = ticker, n
            active[station_id] = best

    print(f"\n{'STATION':8} {'CONFIG':14} {'DISCOVERED':14} MATCH")
    print("-" * 52)
    mismatches = []
    for station in config.stations:
        discovered = active.get(station.id, "(none)")
        match = "ok" if discovered == station.kalshi_series else "MISMATCH"
        if match != "ok":
            mismatches.append(station.id)
        print(f"{station.id:8} {station.kalshi_series:14} {discovered:14} {match}")

    print("-" * 52)
    if mismatches:
        print(f"[WARN] Update config.yaml kalshi_series for: {', '.join(mismatches)}")
        return 1
    print("[OK] config.yaml kalshi_series matches the live active series.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
