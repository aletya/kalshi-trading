"""Backtester: replay GEFS fair values against logged Kalshi order books.

For each logged temperature market we walk its order-book snapshots in time
order, compute the bias-corrected fair value from the GEFS run available at
that moment (no lookahead), and enter the **first** snapshot whose edge clears
the threshold — buying YES at the ask, or NO at 1−bid. The position is held to
resolution and settled against the observed CLI high.

Costs are never hidden: a trade executes at the quote it would really hit (the
ask, or 1−bid), so the bid-ask spread is paid. Each trade also carries the
mid-to-mid counterfactual so the spread's drag is explicit.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from src.common.config import Config
from src.model import bias as bias_model_mod
from src.model.daily_high import local_day_utc_window, member_daily_highs
from src.model.fairvalue import (
    Bucket,
    DEFAULT_MIN_MEMBERS,
    bucket_probability,
    fit_gaussian,
)

# A GEFS cycle is only usable once posted — ~3-5 h after the cycle hour. We use
# a conservative 6 h lag so the backtest never "sees" a run before it existed.
GEFS_AVAILABILITY_LAG = dt.timedelta(hours=6)


# --------------------------------------------------------------------------
# Pure helpers (unit-tested offline)
# --------------------------------------------------------------------------
def bucket_from_strike(
    strike_type: str, floor: float | None, cap: float | None
) -> Bucket | None:
    """Kalshi market strike → continuous fair-value bucket (±0.5 °F correction)."""
    if strike_type == "greater" and floor is not None:
        return Bucket(floor + 0.5, None, f">{floor:g}")
    if strike_type == "less" and cap is not None:
        return Bucket(None, cap - 0.5, f"<{cap:g}")
    if strike_type == "between" and floor is not None and cap is not None:
        return Bucket(floor - 0.5, cap + 0.5, f"{floor:g}-{cap:g}")
    return None


def market_outcome(
    strike_type: str, floor: float | None, cap: float | None, observed_high: float
) -> bool:
    """Did the market resolve YES? Uses Kalshi's exact integer-°F strike rule."""
    if strike_type == "greater":
        return observed_high > floor
    if strike_type == "less":
        return observed_high < cap
    if strike_type == "between":
        return floor <= observed_high <= cap
    raise ValueError(f"unknown strike_type {strike_type!r}")


def evaluate_pnl(
    side: str, yes_bid: float, yes_ask: float, outcome: bool
) -> tuple[float, float, float]:
    """P&L per contract for a settled trade.

    Returns ``(price_paid, pnl, pnl_mid)``. ``price_paid`` is the quote we'd
    really hit (ask for YES, 1−bid for NO). ``pnl_mid`` is the mid-to-mid
    counterfactual — its gap to ``pnl`` is the spread paid.
    """
    yes_mid = (yes_bid + yes_ask) / 2.0
    if side == "YES":
        price_paid = yes_ask
        payoff = 1.0 if outcome else 0.0
        return price_paid, payoff - price_paid, payoff - yes_mid
    if side == "NO":
        price_paid = 1.0 - yes_bid
        payoff = 1.0 if not outcome else 0.0
        return price_paid, payoff - price_paid, payoff - (1.0 - yes_mid)
    raise ValueError(f"side must be YES or NO, got {side!r}")


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class GefsRun:
    init_time: dt.datetime
    path: Path


@dataclass
class Trade:
    ticker: str
    station_id: str
    target_date: dt.date
    bucket_label: str
    decision_ts: dt.datetime
    gefs_init: dt.datetime
    our_prob: float
    yes_bid: float
    yes_ask: float
    side: str
    price_paid: float
    edge: float
    outcome: bool
    pnl: float
    pnl_mid: float

    @property
    def spread_cost(self) -> float:
        return self.pnl_mid - self.pnl


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# GEFS run index + cached fair-value fit
# --------------------------------------------------------------------------
def index_gefs_runs(ensemble_dir: Path) -> list[GefsRun]:
    """All GEFS run Parquets on disk, parsed to (init_time, path), oldest first."""
    runs: list[GefsRun] = []
    for path in ensemble_dir.glob("*/gefs_*.parquet"):
        try:
            _, date_s, cycle_s = path.stem.split("_")
            init = dt.datetime.strptime(date_s, "%Y%m%d").replace(
                hour=int(cycle_s[:2]), tzinfo=dt.timezone.utc
            )
        except (ValueError, IndexError):
            continue
        runs.append(GefsRun(init, path))
    return sorted(runs, key=lambda r: r.init_time)


class _GaussianCache:
    """Lazily fits (and caches) the daily-high Gaussian per (run, station, date)."""

    def __init__(self, bias_model: dict | None):
        self._bias = bias_model
        self._parquet: dict[Path, pl.DataFrame] = {}
        self._fit: dict[tuple, tuple[float, float] | None] = {}

    def gaussian(self, run, station, target_date):
        key = (run.path, station.id, target_date)
        if key in self._fit:
            return self._fit[key]
        df = self._parquet.get(run.path)
        if df is None:
            df = pl.read_parquet(run.path)
            self._parquet[run.path] = df
        raw = sorted(member_daily_highs(df, station, target_date).values())
        if len(raw) < DEFAULT_MIN_MEMBERS:
            self._fit[key] = None
            return None
        if self._bias is not None:
            raw = bias_model_mod.apply_bias(self._bias, station.id, target_date, raw)
        self._fit[key] = fit_gaussian(raw)
        return self._fit[key]


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------
def _load_observed_highs(path: Path) -> dict[tuple[str, dt.date], float]:
    df = pl.read_parquet(path)
    return {
        (r["station_id"], r["date"]): r["observed_high_f"]
        for r in df.iter_rows(named=True)
    }


def run_backtest(
    config: Config,
    *,
    db_path: Path | None = None,
    gefs_dir: Path | None = None,
    observations_path: Path | None = None,
    bias_model: dict | None = None,
) -> BacktestResult:
    """Replay every logged market and return the trades the strategy would take."""
    db_path = db_path or config.paths.database
    gefs_dir = gefs_dir or config.paths.ensemble_dir
    observations_path = observations_path or (
        config.paths.observations_dir / "observed_highs.parquet"
    )

    observed = _load_observed_highs(observations_path)
    runs = index_gefs_runs(gefs_dir)
    cache = _GaussianCache(bias_model)
    min_edge = config.strategy.min_edge
    require_gt_spread = config.strategy.require_edge_exceeds_spread

    skipped: Counter = Counter()
    trades: list[Trade] = []

    conn = sqlite3.connect(db_path)
    try:
        markets = conn.execute(
            "SELECT ticker, station_id, target_date, strike_type, "
            "floor_strike, cap_strike FROM markets"
        ).fetchall()
        for ticker, station_id, target_s, strike_type, floor, cap in markets:
            if not target_s:
                skipped["bad_market"] += 1
                continue
            target_date = dt.date.fromisoformat(target_s)
            bucket = bucket_from_strike(strike_type, floor, cap)
            if bucket is None:
                skipped["bad_strike"] += 1
                continue
            obs = observed.get((station_id, target_date))
            if obs is None:
                skipped["unresolved"] += 1  # market not yet settled / no CLI
                continue

            station = config.station(station_id)
            outcome = market_outcome(strike_type, floor, cap, obs)
            # No lookahead: only decide before the target local day begins.
            day_start, _ = local_day_utc_window(target_date, station.timezone)

            outcome_or_reason = _first_qualifying_trade(
                conn, ticker, station, target_date, bucket, outcome,
                day_start, runs, cache, min_edge, require_gt_spread,
            )
            if isinstance(outcome_or_reason, Trade):
                trades.append(outcome_or_reason)
            else:
                skipped[outcome_or_reason] += 1
    finally:
        conn.close()

    return BacktestResult(trades=trades, skipped=dict(skipped))


def _first_qualifying_trade(
    conn, ticker, station, target_date, bucket, outcome,
    day_start, runs, cache, min_edge, require_gt_spread,
) -> "Trade | str":
    """Return the trade taken, or a skip reason: 'no_edge' (an edge was
    evaluated but never cleared the threshold) vs 'no_decision_window' (no
    lookahead-safe, two-sided, GEFS-covered snapshot ever existed)."""
    snapshots = conn.execute(
        "SELECT ts, yes_bid, yes_ask FROM orderbook_snapshots "
        "WHERE ticker = ? ORDER BY ts",
        (ticker,),
    ).fetchall()

    evaluated = False
    for ts_s, yes_bid, yes_ask in snapshots:
        ts = dt.datetime.fromisoformat(ts_s)
        if ts >= day_start:
            break  # snapshots from here on are not lookahead-safe
        if yes_bid is None or yes_ask is None:
            continue  # need a two-sided quote to trade

        gauss = _fair_value(ts, runs, cache, station, target_date)
        if gauss is None:
            continue
        evaluated = True
        (mu, sigma), gefs_init = gauss
        prob = bucket_probability(mu, sigma, bucket)
        yes_mid = (yes_bid + yes_ask) / 2.0
        spread = yes_ask - yes_bid

        side = None
        if prob - yes_ask >= min_edge:
            side, edge = "YES", prob - yes_mid
        elif yes_bid - prob >= min_edge:
            side, edge = "NO", yes_mid - prob
        if side is None:
            continue
        if require_gt_spread and edge <= spread:
            continue

        price_paid, pnl, pnl_mid = evaluate_pnl(side, yes_bid, yes_ask, outcome)
        return Trade(
            ticker=ticker, station_id=station.id, target_date=target_date,
            bucket_label=bucket.label, decision_ts=ts, gefs_init=gefs_init,
            our_prob=prob, yes_bid=yes_bid, yes_ask=yes_ask, side=side,
            price_paid=price_paid, edge=edge, outcome=outcome,
            pnl=pnl, pnl_mid=pnl_mid,
        )
    return "no_edge" if evaluated else "no_decision_window"


def _fair_value(ts, runs, cache, station, target_date):
    """Most recent GEFS run available at ``ts`` that covers ``target_date``."""
    for run in reversed(runs):
        if run.init_time + GEFS_AVAILABILITY_LAG > ts:
            continue
        gauss = cache.gaussian(run, station, target_date)
        if gauss is not None:
            return gauss, run.init_time
    return None
