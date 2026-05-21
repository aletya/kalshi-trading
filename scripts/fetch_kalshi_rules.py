"""Read-only research helper: report Kalshi's temperature-market resolution rules.

Pulls the live Kalshi weather series, extracts the exact NWS CLI settlement
station for each city from each series' ``settlement_sources``, and reconciles
it against config.yaml. Use this to populate / re-verify the ``resolution_*``
fields in config.yaml.

This is a self-contained verification helper — NOT the Phase 4 order-book
logger. It only reads public market metadata; no auth, no trading.

    python scripts/fetch_kalshi_rules.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from src.common.config import load_config  # noqa: E402

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"


def parse_issuedby(settlement_url: str) -> str | None:
    """Extract the CLI station code (?issuedby=XXX) from a settlement-source URL."""
    if not settlement_url:
        return None
    values = parse_qs(urlparse(settlement_url).query).get("issuedby")
    return values[0].upper() if values else None


def is_high_temp_series(series: dict) -> bool:
    """True for single-city daily high-temperature series."""
    title = series.get("title", "").lower()
    if not ("high" in title or "max" in title):
        return False
    # Exclude aggregate / non-city markets.
    excluded = ("united states", "cities", "100", "arctic", "sea ice")
    return not any(word in title for word in excluded)


def fetch_weather_series(client: httpx.Client) -> list[dict]:
    resp = client.get(
        f"{KALSHI_API}/series", params={"category": "Climate and Weather"}
    )
    resp.raise_for_status()
    return resp.json().get("series", [])


def main() -> int:
    config = load_config()
    print("Fetching Kalshi weather series ...")

    with httpx.Client(timeout=30.0) as client:
        series = [s for s in fetch_weather_series(client) if is_high_temp_series(s)]

    # issuedby (CLI station code) -> {name, url, [series_tickers]}
    by_station: dict[str, dict] = {}
    for s in sorted(series, key=lambda x: x["ticker"]):
        src = (s.get("settlement_sources") or [{}])[0]
        issuedby = parse_issuedby(src.get("url", ""))
        if not issuedby:
            continue
        entry = by_station.setdefault(
            issuedby,
            {"name": src.get("name", "-"), "url": src.get("url", ""), "tickers": []},
        )
        entry["tickers"].append(s["ticker"])

    print(f"Found settlement stations for {len(by_station)} CLI sites.\n")
    print(f"{'CONFIG':7} {'KALSHI':7} {'MATCH':7} SETTLEMENT SOURCE")
    print("-" * 78)

    mismatches: list[str] = []
    for station in config.stations:
        issuedby = station.id.lstrip("K")  # config ids are K + CLI code
        entry = by_station.get(issuedby)
        if entry is None:
            status = "NO DATA"
            mismatches.append(f"{station.id}: no Kalshi series found")
            print(f"{station.id:7} {'-':7} {status:7} (no matching Kalshi series)")
            continue
        match = "ok" if station.resolution_verified else "CHECK"
        print(f"{station.id:7} {issuedby:7} {match:7} {entry['name']}")
        print(f"{'':23} {entry['url']}")
        print(f"{'':23} series: {', '.join(entry['tickers'])}")

    print("-" * 78)
    if mismatches:
        print("[WARN] Issues to resolve:")
        for m in mismatches:
            print(f"  - {m}")
    else:
        print("[OK] Every config station maps to a Kalshi CLI settlement site.")
    print(
        "\nReview the settlement sources above against config.yaml's "
        "resolution_station / resolution_notes fields."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
