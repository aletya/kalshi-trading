"""Unit + integration tests for src/backtest/engine.py."""

import datetime as dt

import polars as pl
import pytest

from src.backtest import engine
from src.common.config import load_config
from src.ingest import kalshi

UTC = dt.timezone.utc


# --- bucket_from_strike ---------------------------------------------------
def test_bucket_from_strike_continuity_correction():
    assert engine.bucket_from_strike("greater", 70, None) == engine.Bucket(70.5, None, ">70")
    assert engine.bucket_from_strike("less", None, 63) == engine.Bucket(None, 62.5, "<63")
    assert engine.bucket_from_strike("between", 69, 70) == engine.Bucket(68.5, 70.5, "69-70")
    assert engine.bucket_from_strike("greater", None, None) is None


# --- market_outcome (Kalshi's exact integer rule) -------------------------
def test_market_outcome_greater():
    assert engine.market_outcome("greater", 70, None, 71) is True
    assert engine.market_outcome("greater", 70, None, 70) is False


def test_market_outcome_less():
    assert engine.market_outcome("less", None, 63, 62) is True
    assert engine.market_outcome("less", None, 63, 63) is False


def test_market_outcome_between_is_inclusive():
    assert engine.market_outcome("between", 69, 70, 69) is True
    assert engine.market_outcome("between", 69, 70, 70) is True
    assert engine.market_outcome("between", 69, 70, 71) is False


# --- evaluate_pnl (the spread must always be paid) ------------------------
def test_pnl_buy_yes_win():
    price, pnl, pnl_mid = engine.evaluate_pnl("YES", 0.40, 0.50, outcome=True)
    assert price == 0.50
    assert pnl == pytest.approx(0.50)        # 1.00 - 0.50
    assert pnl_mid == pytest.approx(0.55)    # 1.00 - mid(0.45)
    assert pnl < pnl_mid                     # the spread cost real money


def test_pnl_buy_yes_loss():
    price, pnl, pnl_mid = engine.evaluate_pnl("YES", 0.40, 0.50, outcome=False)
    assert pnl == pytest.approx(-0.50)
    assert pnl_mid == pytest.approx(-0.45)


def test_pnl_buy_no_win():
    # Buy NO at 1 - bid = 0.60; outcome NO -> payoff 1.
    price, pnl, pnl_mid = engine.evaluate_pnl("NO", 0.40, 0.50, outcome=False)
    assert price == pytest.approx(0.60)
    assert pnl == pytest.approx(0.40)
    assert pnl < pnl_mid


def test_pnl_unknown_side_raises():
    with pytest.raises(ValueError):
        engine.evaluate_pnl("MAYBE", 0.4, 0.5, True)


# --- run_backtest end to end (synthetic data on disk) ---------------------
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
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)


def test_run_backtest_end_to_end(tmp_path):
    config = load_config()

    # GEFS run, available before the 2026-07-16 NYC day, covering it.
    gefs_dir = tmp_path / "ensemble"
    _write_synthetic_gefs(gefs_dir / "20260715" / "gefs_20260715_00z.parquet")

    # Observed high 80 °F -> a ">70" market resolves YES.
    obs_path = tmp_path / "observed_highs.parquet"
    pl.DataFrame([{
        "station_id": "KNYC", "date": dt.date(2026, 7, 16), "observed_high_f": 80.0,
    }]).write_parquet(obs_path)

    # One market + one snapshot quoted cheap (ask 0.50) before the target day.
    db_path = tmp_path / "kalshi.db"
    conn = kalshi.connect(db_path)
    kalshi.upsert_market(
        conn,
        {"ticker": "KXHIGHNY-26JUL16-T70", "series_ticker": "KXHIGHNY",
         "strike_type": "greater", "floor_strike": 70, "cap_strike": None,
         "title": ">70"},
        "KNYC", "2026-07-15T00:00:00+00:00",
    )
    kalshi.insert_snapshot(
        conn, "KXHIGHNY-26JUL16-T70", "2026-07-15T12:00:00+00:00", "active",
        kalshi.Quote(0.48, 0.50, 100.0, 100.0), {},
    )
    conn.commit()
    conn.close()

    result = engine.run_backtest(
        config, db_path=db_path, gefs_dir=gefs_dir,
        observations_path=obs_path, bias_model=None,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "YES"               # our prob >> the 0.50 ask
    assert trade.outcome is True             # 80 °F > 70
    assert trade.price_paid == 0.50
    assert trade.pnl == pytest.approx(0.50)  # won: 1.00 - 0.50


def test_run_backtest_skips_unresolved_markets(tmp_path):
    """A market with no observation yet is skipped, not traded."""
    config = load_config()
    obs_path = tmp_path / "observed_highs.parquet"
    pl.DataFrame(
        [], schema={"station_id": pl.String, "date": pl.Date, "observed_high_f": pl.Float64}
    ).write_parquet(obs_path)

    db_path = tmp_path / "kalshi.db"
    conn = kalshi.connect(db_path)
    kalshi.upsert_market(
        conn,
        {"ticker": "KXHIGHNY-26JUL16-T70", "strike_type": "greater",
         "floor_strike": 70, "cap_strike": None, "title": ">70"},
        "KNYC", "2026-07-15T00:00:00+00:00",
    )
    conn.commit()
    conn.close()

    result = engine.run_backtest(
        config, db_path=db_path, gefs_dir=tmp_path / "ensemble",
        observations_path=obs_path, bias_model=None,
    )
    assert result.trades == []
    assert result.skipped.get("unresolved") == 1
