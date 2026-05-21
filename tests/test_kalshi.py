"""Offline unit tests for src/ingest/kalshi.py.

No network: order-book parsing, ticker parsing, and an in-memory SQLite
round-trip. The live polling pass is exercised by log_kalshi.py at Checkpoint 4.
"""

import sqlite3

import pytest

from src.ingest import kalshi


# --- parse_orderbook ------------------------------------------------------
def test_parse_orderbook_both_sides():
    book = {
        "yes_dollars": [["0.0100", "100"], ["0.0200", "50"]],
        "no_dollars": [["0.9500", "10"], ["0.9700", "20"]],
    }
    q = kalshi.parse_orderbook(book)
    assert q.yes_bid == pytest.approx(0.02) and q.yes_bid_qty == 50.0
    # best no bid 0.97 -> yes ask = 1 - 0.97 = 0.03
    assert q.yes_ask == pytest.approx(0.03) and q.yes_ask_qty == 20.0
    assert q.mid == pytest.approx(0.025)
    assert q.spread == pytest.approx(0.01)


def test_parse_orderbook_empty_yes_side():
    q = kalshi.parse_orderbook({"yes_dollars": [], "no_dollars": [["0.90", "5"]]})
    assert q.yes_bid is None
    assert q.yes_ask == pytest.approx(0.10)
    assert q.mid is None and q.spread is None


def test_parse_orderbook_completely_empty():
    q = kalshi.parse_orderbook({})
    assert q.yes_bid is None and q.yes_ask is None
    assert q.mid is None and q.spread is None


# --- parse_target_date ----------------------------------------------------
def test_parse_target_date():
    assert kalshi.parse_target_date("KXHIGHNY-26MAY22-T70") == "2026-05-22"
    assert kalshi.parse_target_date("KXHIGHTPHX-26DEC01-B94.5") == "2026-12-01"


def test_parse_target_date_bad_ticker():
    assert kalshi.parse_target_date("NOTATICKER") is None
    assert kalshi.parse_target_date("KX-26XXX99-T1") is None


# --- SQLite round-trip ----------------------------------------------------
def _market():
    return {
        "ticker": "KXHIGHNY-26MAY22-T70",
        "series_ticker": "KXHIGHNY",
        "strike_type": "greater",
        "floor_strike": 70,
        "cap_strike": None,
        "title": "Will the high temp in NYC be >70 on May 22, 2026?",
        "status": "active",
    }


def test_init_db_creates_tables():
    conn = sqlite3.connect(":memory:")
    kalshi.init_db(conn)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"markets", "orderbook_snapshots"} <= tables


def test_upsert_market_keeps_first_seen_updates_last_seen():
    conn = sqlite3.connect(":memory:")
    kalshi.init_db(conn)
    kalshi.upsert_market(conn, _market(), "KNYC", "2026-05-21T00:00:00+00:00")
    kalshi.upsert_market(conn, _market(), "KNYC", "2026-05-21T00:15:00+00:00")
    row = conn.execute(
        "SELECT first_seen, last_seen, station_id, target_date FROM markets"
    ).fetchone()
    assert row[0] == "2026-05-21T00:00:00+00:00"  # first_seen unchanged
    assert row[1] == "2026-05-21T00:15:00+00:00"  # last_seen advanced
    assert row[2] == "KNYC"
    assert row[3] == "2026-05-22"


def test_insert_snapshot_is_idempotent_per_ticker_ts():
    conn = sqlite3.connect(":memory:")
    kalshi.init_db(conn)
    quote = kalshi.Quote(0.02, 0.03, 50.0, 20.0)
    ticker, ts = "KXHIGHNY-26MAY22-T70", "2026-05-21T00:00:00+00:00"
    assert kalshi.insert_snapshot(conn, ticker, ts, "active", quote, {}) is True
    # Same (ticker, ts) again -> ignored.
    assert kalshi.insert_snapshot(conn, ticker, ts, "active", quote, {}) is False
    count = conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    assert count == 1


def test_snapshot_stores_spread_for_later_query():
    conn = sqlite3.connect(":memory:")
    kalshi.init_db(conn)
    for i, ts in enumerate(["2026-05-21T00:00:00", "2026-05-21T00:15:00"]):
        quote = kalshi.Quote(0.02, 0.03 + i * 0.01, 50.0, 20.0)
        kalshi.insert_snapshot(conn, "KXHIGHNY-26MAY22-T70", ts, "active", quote, {})
    spreads = [
        r[0] for r in conn.execute(
            "SELECT spread FROM orderbook_snapshots ORDER BY ts"
        )
    ]
    assert [round(s, 4) for s in spreads] == [0.01, 0.02]
