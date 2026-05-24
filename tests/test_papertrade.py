"""Tests for src/papertrade/runner.py — table round-trip + an integration cycle."""

import datetime as dt
import sqlite3

import polars as pl
import pytest

from src.common.config import load_config
from src.ingest import kalshi
from src.papertrade import runner

UTC = dt.timezone.utc


# --- Table round-trip -----------------------------------------------------
def _example_trade():
    return {
        "ticker": "KXHIGHNY-26JUL16-T70", "station_id": "KNYC",
        "target_date": "2026-07-16", "strike_type": "greater",
        "floor_strike": 70, "cap_strike": None, "bucket_label": ">70",
        "entry_ts": "2026-07-15T12:00:00+00:00",
        "gefs_init": "2026-07-15T00:00:00+00:00",
        "our_prob": 0.92, "side": "YES",
        "entry_yes_bid": 0.48, "entry_yes_ask": 0.50,
        "entry_price": 0.50, "edge": 0.43, "status": "open",
    }


def test_init_paper_table_is_idempotent():
    conn = sqlite3.connect(":memory:")
    runner.init_paper_table(conn)
    runner.init_paper_table(conn)  # should not raise
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "paper_trades" in tables


def test_record_and_existing_positions_round_trip():
    conn = sqlite3.connect(":memory:")
    runner.init_paper_table(conn)
    runner.record_trade(conn, _example_trade())
    assert runner.existing_positions(conn) == {"KXHIGHNY-26JUL16-T70"}


def test_open_positions_returns_typed_rows():
    conn = sqlite3.connect(":memory:")
    runner.init_paper_table(conn)
    runner.record_trade(conn, _example_trade())
    opens = runner.open_positions(conn)
    assert len(opens) == 1
    pos = opens[0]
    assert pos.target_date == dt.date(2026, 7, 16)
    assert pos.side == "YES"
    assert pos.entry_yes_ask == 0.50


def test_settle_position_updates_row():
    conn = sqlite3.connect(":memory:")
    runner.init_paper_table(conn)
    runner.record_trade(conn, _example_trade())
    runner.settle_position(
        conn, "KXHIGHNY-26JUL16-T70",
        observed_high=80.0, outcome=True, pnl=0.50, pnl_mid=0.51,
        settled_ts="2026-07-17T06:00:00+00:00",
    )
    row = conn.execute(
        "SELECT status, observed_high, outcome, pnl FROM paper_trades"
    ).fetchone()
    assert row == ("settled", 80.0, 1, 0.50)
    # A settled ticker is still in existing_positions (never re-traded).
    assert "KXHIGHNY-26JUL16-T70" in runner.existing_positions(conn)


# --- paper_trade_once integration cycle -----------------------------------
def _write_synthetic_gefs(path):
    """12-member ensemble; member k's NYC daily high for 2026-07-16 is 70+k."""
    hours = [dt.datetime(2026, 7, 16, h, tzinfo=UTC) for h in (6, 9, 12, 15, 18, 21)]
    hours += [dt.datetime(2026, 7, 17, 0, tzinfo=UTC), dt.datetime(2026, 7, 17, 3, tzinfo=UTC)]
    rows = []
    for k in range(12):
        for i, t in enumerate(hours):
            rows.append({
                "station_id": "KNYC", "member": f"m{k:02d}",
                "valid_time": t, "temp_2m_f": (70.0 + k) if i == 3 else (55.0 + k),
                # gaussian cache reads init_time from the df
                "init_time": dt.datetime(2026, 7, 15, 0, tzinfo=UTC),
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)


def _seed_db(db_path):
    conn = kalshi.connect(db_path)
    kalshi.upsert_market(
        conn,
        {"ticker": "KXHIGHNY-26JUL16-T70", "series_ticker": "KXHIGHNY",
         "strike_type": "greater", "floor_strike": 70, "cap_strike": None,
         "title": ">70"},
        "KNYC", "2026-07-15T00:00:00+00:00",
    )
    kalshi.insert_snapshot(
        conn, "KXHIGHNY-26JUL16-T70", "2026-07-15T11:00:00+00:00", "active",
        kalshi.Quote(0.48, 0.50, 100.0, 100.0), {},
    )
    conn.commit()
    return conn


def _patch_paths(tmp_path, config):
    """Point a copy of the loaded Config at tmp_path's directories."""
    new_paths = config.paths.__class__(
        data_dir=tmp_path,
        ensemble_dir=tmp_path / "ensemble",
        observations_dir=tmp_path / "observations",
        database=tmp_path / "kalshi.db",
    )
    return config.__class__(
        stations=config.stations, gefs=config.gefs, kalshi=config.kalshi,
        strategy=config.strategy, paths=new_paths,
    )


def test_paper_trade_once_opens_then_settles(tmp_path):
    config = _patch_paths(tmp_path, load_config())
    _write_synthetic_gefs(tmp_path / "ensemble" / "20260715" / "gefs_20260715_00z.parquet")
    db_path = tmp_path / "kalshi.db"
    conn = _seed_db(db_path)

    # First cycle: no observation yet -> trade opens.
    decision_time = dt.datetime(2026, 7, 15, 12, tzinfo=UTC)
    result = runner.paper_trade_once(
        config, conn, bias_model=None, observations={}, now=decision_time,
    )
    assert result.opened == 1
    assert result.settled == 0
    open_rows = runner.open_positions(conn)
    assert len(open_rows) == 1 and open_rows[0].side == "YES"

    # Second cycle: observation publishes -> position settles, no new trade.
    settle_time = dt.datetime(2026, 7, 17, 6, tzinfo=UTC)
    observed = {("KNYC", dt.date(2026, 7, 16)): 80.0}  # > 70 -> YES wins
    result2 = runner.paper_trade_once(
        config, conn, bias_model=None, observations=observed, now=settle_time,
    )
    assert result2.opened == 0
    assert result2.settled == 1
    row = conn.execute(
        "SELECT status, outcome, pnl FROM paper_trades"
    ).fetchone()
    assert row[0] == "settled"
    assert row[1] == 1
    assert row[2] == pytest.approx(0.50)  # 1.00 - 0.50 ask
    conn.close()
