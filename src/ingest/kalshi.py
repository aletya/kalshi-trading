"""Kalshi market ingestion: snapshot temperature order books into SQLite.

Live quotes come from the orderbook endpoint, not the markets-list summary
(which never populates yes_bid/yes_ask). The book has a yes side and a no side,
each a price-ascending ladder of [price, quantity]; the best yes ask is
1 - (best no bid), since selling YES is the same as buying NO.

This module is REST-only; a WebSocket feed is a possible later addition.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from src.common.config import Config

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


# --------------------------------------------------------------------------
# Order-book parsing (pure — unit-tested offline)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Quote:
    """Top-of-book for one market, in probability units (0-1)."""

    yes_bid: float | None
    yes_ask: float | None
    yes_bid_qty: float | None
    yes_ask_qty: float | None

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid


def parse_orderbook(orderbook_fp: dict) -> Quote:
    """Best yes bid/ask from a Kalshi ``orderbook_fp`` payload.

    Each side is a list of ``[price, quantity]`` strings sorted ascending by
    price; the last entry is the best (highest) bid on that side.
    """
    yes = orderbook_fp.get("yes_dollars") or []
    no = orderbook_fp.get("no_dollars") or []
    # Kalshi prices are whole cents; round to clear binary-float noise.
    yes_bid = round(float(yes[-1][0]), 4) if yes else None
    yes_bid_qty = float(yes[-1][1]) if yes else None
    if no:
        yes_ask = round(1.0 - float(no[-1][0]), 4)
        yes_ask_qty = float(no[-1][1])
    else:
        yes_ask = yes_ask_qty = None
    return Quote(yes_bid, yes_ask, yes_bid_qty, yes_ask_qty)


def parse_target_date(ticker: str) -> str | None:
    """Resolution date (ISO) from a market ticker, e.g. KXHIGHNY-26MAY22-T70."""
    parts = ticker.split("-")
    if len(parts) < 2 or len(parts[1]) < 7:
        return None
    seg = parts[1]
    try:
        return dt.date(2000 + int(seg[:2]), _MONTHS[seg[2:5]], int(seg[5:7])).isoformat()
    except (KeyError, ValueError):
        return None


# --------------------------------------------------------------------------
# REST fetching (throttled, with 429 backoff)
# --------------------------------------------------------------------------
def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    """GET with exponential backoff on HTTP 429 (rate limit)."""
    for attempt in range(5):
        resp = client.get(url, params=params)
        if resp.status_code == 429:
            time.sleep(2.0**attempt)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def fetch_markets(client: httpx.Client, api_base: str, series_ticker: str) -> list[dict]:
    """All currently-tradeable markets for a series."""
    resp = _get(
        client,
        f"{api_base}/markets",
        params={"series_ticker": series_ticker, "limit": 200},
    )
    markets = resp.json().get("markets", [])
    return [m for m in markets if m.get("status") in ("active", "open")]


def fetch_orderbook(client: httpx.Client, api_base: str, ticker: str) -> dict:
    """Raw ``orderbook_fp`` payload for one market."""
    resp = _get(client, f"{api_base}/markets/{ticker}/orderbook")
    return resp.json().get("orderbook_fp") or {}


# --------------------------------------------------------------------------
# SQLite storage
# --------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    ticker         TEXT PRIMARY KEY,
    series_ticker  TEXT,
    station_id     TEXT,
    target_date    TEXT,
    strike_type    TEXT,
    floor_strike   REAL,
    cap_strike     REAL,
    title          TEXT,
    first_seen     TEXT,
    last_seen      TEXT
);
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    ts            TEXT NOT NULL,
    status        TEXT,
    yes_bid       REAL,
    yes_ask       REAL,
    yes_bid_qty   REAL,
    yes_ask_qty   REAL,
    mid           REAL,
    spread        REAL,
    raw_orderbook TEXT,
    UNIQUE(ticker, ts)
);
CREATE INDEX IF NOT EXISTS idx_snap_ticker_ts
    ON orderbook_snapshots(ticker, ts);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite db and ensure the schema exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the order-book tables if they do not exist (idempotent)."""
    conn.executescript(_SCHEMA)
    conn.commit()


def upsert_market(
    conn: sqlite3.Connection, market: dict, station_id: str, now: str
) -> None:
    """Insert a market row, or refresh its mutable fields + ``last_seen``."""
    conn.execute(
        """
        INSERT INTO markets (ticker, series_ticker, station_id, target_date,
                             strike_type, floor_strike, cap_strike, title,
                             first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            title = excluded.title,
            strike_type = excluded.strike_type,
            floor_strike = excluded.floor_strike,
            cap_strike = excluded.cap_strike,
            last_seen = excluded.last_seen
        """,
        (
            market["ticker"],
            market.get("series_ticker"),
            station_id,
            parse_target_date(market["ticker"]),
            market.get("strike_type"),
            market.get("floor_strike"),
            market.get("cap_strike"),
            market.get("title"),
            now,
            now,
        ),
    )


def insert_snapshot(
    conn: sqlite3.Connection,
    ticker: str,
    ts: str,
    status: str | None,
    quote: Quote,
    orderbook_fp: dict,
) -> bool:
    """Append one order-book snapshot. Returns False if (ticker, ts) already logged."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO orderbook_snapshots
            (ticker, ts, status, yes_bid, yes_ask, yes_bid_qty, yes_ask_qty,
             mid, spread, raw_orderbook)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            ts,
            status,
            quote.yes_bid,
            quote.yes_ask,
            quote.yes_bid_qty,
            quote.yes_ask_qty,
            quote.mid,
            quote.spread,
            json.dumps(orderbook_fp, separators=(",", ":")),
        ),
    )
    return cursor.rowcount > 0


# --------------------------------------------------------------------------
# One polling pass
# --------------------------------------------------------------------------
@dataclass
class LogResult:
    markets_seen: int = 0
    snapshots_inserted: int = 0
    errors: list[str] = field(default_factory=list)


def log_once(
    config: Config,
    conn: sqlite3.Connection,
    throttle_s: float = 0.12,
    verbose: bool = False,
) -> LogResult:
    """One full polling pass: snapshot every configured market's order book."""
    result = LogResult()
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    api = config.kalshi.api_base

    with httpx.Client(timeout=30.0) as client:
        for station in config.stations:
            series = station.kalshi_series
            if not series:
                continue
            try:
                markets = fetch_markets(client, api, series)
            except httpx.HTTPError as exc:
                result.errors.append(f"{series}: market list failed ({exc})")
                continue

            for market in markets:
                ticker = market["ticker"]
                upsert_market(conn, market, station.id, ts)
                try:
                    time.sleep(throttle_s)
                    orderbook = fetch_orderbook(client, api, ticker)
                except httpx.HTTPError as exc:
                    result.errors.append(f"{ticker}: orderbook failed ({exc})")
                    continue
                inserted = insert_snapshot(
                    conn, ticker, ts, market.get("status"),
                    parse_orderbook(orderbook), orderbook,
                )
                result.markets_seen += 1
                result.snapshots_inserted += int(inserted)

            if verbose:
                print(f"  {station.id} ({series}): {len(markets)} markets")

    conn.commit()
    return result
