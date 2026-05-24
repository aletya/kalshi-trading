"""Paper trading loop: record the trades we would place, settle them later.

Each cycle reads the latest order-book snapshot per market from the Phase 4
logger's `kalshi.db`, computes the bias-corrected fair value from the latest
GEFS run, applies the same trade rule as the Phase 5 backtester, and persists
qualifying trades to a new ``paper_trades`` table. Open positions are settled
against the observed CLI high when one becomes available.

No real orders are placed — no Kalshi trading API calls are made anywhere.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from src.backtest import engine
from src.common.config import Config
from src.model.daily_high import local_day_utc_window
from src.model.fairvalue import bucket_probability

_PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL UNIQUE,
    station_id      TEXT,
    target_date     TEXT,
    strike_type     TEXT,
    floor_strike    REAL,
    cap_strike      REAL,
    bucket_label    TEXT,
    entry_ts        TEXT,
    gefs_init       TEXT,
    our_prob        REAL,
    side            TEXT,
    entry_yes_bid   REAL,
    entry_yes_ask   REAL,
    entry_price     REAL,
    edge            REAL,
    status          TEXT,
    observed_high   REAL,
    outcome         INTEGER,
    pnl             REAL,
    pnl_mid         REAL,
    settled_ts      TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades(status);
"""


@dataclass
class OpenPosition:
    ticker: str
    station_id: str
    target_date: dt.date
    strike_type: str
    floor_strike: float | None
    cap_strike: float | None
    side: str
    entry_yes_bid: float
    entry_yes_ask: float


@dataclass
class PaperResult:
    opened: int = 0
    settled: int = 0
    skipped: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------
def init_paper_table(conn: sqlite3.Connection) -> None:
    """Create the paper_trades table if it doesn't already exist."""
    conn.executescript(_PAPER_SCHEMA)
    conn.commit()


def existing_positions(conn: sqlite3.Connection) -> set[str]:
    """Tickers we already hold (open or settled) — never re-traded."""
    return {row[0] for row in conn.execute("SELECT ticker FROM paper_trades")}


def open_positions(conn: sqlite3.Connection) -> list[OpenPosition]:
    rows = conn.execute(
        "SELECT ticker, station_id, target_date, strike_type, floor_strike, "
        "cap_strike, side, entry_yes_bid, entry_yes_ask "
        "FROM paper_trades WHERE status = 'open'"
    ).fetchall()
    return [
        OpenPosition(
            ticker=r[0], station_id=r[1],
            target_date=dt.date.fromisoformat(r[2]),
            strike_type=r[3], floor_strike=r[4], cap_strike=r[5],
            side=r[6], entry_yes_bid=r[7], entry_yes_ask=r[8],
        )
        for r in rows
    ]


def record_trade(conn: sqlite3.Connection, trade: dict) -> None:
    cols = ", ".join(trade)
    placeholders = ", ".join("?" * len(trade))
    conn.execute(
        f"INSERT INTO paper_trades ({cols}) VALUES ({placeholders})",
        tuple(trade.values()),
    )


def settle_position(
    conn: sqlite3.Connection,
    ticker: str,
    observed_high: float,
    outcome: bool,
    pnl: float,
    pnl_mid: float,
    settled_ts: str,
) -> None:
    conn.execute(
        "UPDATE paper_trades SET status='settled', observed_high=?, outcome=?, "
        "pnl=?, pnl_mid=?, settled_ts=? WHERE ticker=?",
        (observed_high, int(outcome), pnl, pnl_mid, settled_ts, ticker),
    )


# --------------------------------------------------------------------------
# One decide + settle pass
# --------------------------------------------------------------------------
def paper_trade_once(
    config: Config,
    conn: sqlite3.Connection,
    bias_model: dict | None,
    observations: dict[tuple[str, dt.date], float],
    now: dt.datetime | None = None,
) -> PaperResult:
    """One full paper-trading cycle: decide new trades, then settle ready ones."""
    init_paper_table(conn)
    now = now or dt.datetime.now(dt.timezone.utc)
    now_iso = now.isoformat()
    result = PaperResult()
    skipped: Counter = Counter()

    held = existing_positions(conn)
    runs = engine.index_gefs_runs(config.paths.ensemble_dir)
    cache = engine._GaussianCache(bias_model)
    min_edge = config.strategy.min_edge
    require_gt_spread = config.strategy.require_edge_exceeds_spread

    # --- decide new trades --------------------------------------------------
    markets = conn.execute(
        "SELECT ticker, station_id, target_date, strike_type, "
        "floor_strike, cap_strike FROM markets"
    ).fetchall()
    for ticker, station_id, target_s, strike_type, floor, cap in markets:
        if ticker in held:
            skipped["already_held"] += 1
            continue
        if not target_s:
            skipped["bad_market"] += 1
            continue
        target_date = dt.date.fromisoformat(target_s)
        try:
            station = config.station(station_id)
        except KeyError:
            skipped["unknown_station"] += 1
            continue
        day_start, _ = local_day_utc_window(target_date, station.timezone)
        if now >= day_start:
            skipped["target_day_started"] += 1   # too late to enter cleanly
            continue
        bucket = engine.bucket_from_strike(strike_type, floor, cap)
        if bucket is None:
            skipped["bad_strike"] += 1
            continue

        snap = conn.execute(
            "SELECT yes_bid, yes_ask FROM orderbook_snapshots "
            "WHERE ticker = ? ORDER BY ts DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if snap is None:
            skipped["no_snapshot"] += 1
            continue
        yes_bid, yes_ask = snap
        if yes_bid is None or yes_ask is None:
            skipped["one_sided"] += 1
            continue

        gauss = None
        for run in reversed(runs):
            g = cache.gaussian(run, station, target_date)
            if g is not None:
                gauss = (g, run.init_time)
                break
        if gauss is None:
            skipped["no_gefs_coverage"] += 1
            continue
        (mu, sigma), gefs_init = gauss
        prob = bucket_probability(mu, sigma, bucket)

        decision = engine.decide_trade(
            prob, yes_bid, yes_ask, min_edge, require_gt_spread
        )
        if decision is None:
            skipped["no_edge"] += 1
            continue
        side, edge = decision
        entry_price = yes_ask if side == "YES" else 1.0 - yes_bid

        record_trade(conn, {
            "ticker": ticker,
            "station_id": station_id,
            "target_date": target_s,
            "strike_type": strike_type,
            "floor_strike": floor,
            "cap_strike": cap,
            "bucket_label": bucket.label,
            "entry_ts": now_iso,
            "gefs_init": gefs_init.isoformat(),
            "our_prob": prob,
            "side": side,
            "entry_yes_bid": yes_bid,
            "entry_yes_ask": yes_ask,
            "entry_price": entry_price,
            "edge": edge,
            "status": "open",
        })
        result.opened += 1

    # --- settle open positions whose outcomes have published ---------------
    for pos in open_positions(conn):
        observed = observations.get((pos.station_id, pos.target_date))
        if observed is None:
            continue
        outcome = engine.market_outcome(
            pos.strike_type, pos.floor_strike, pos.cap_strike, observed
        )
        _, pnl, pnl_mid = engine.evaluate_pnl(
            pos.side, pos.entry_yes_bid, pos.entry_yes_ask, outcome
        )
        settle_position(conn, pos.ticker, observed, outcome, pnl, pnl_mid, now_iso)
        result.settled += 1

    conn.commit()
    result.skipped = dict(skipped)
    return result
