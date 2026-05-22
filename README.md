# Kalshi Weather Trading Research System

A research system that tests whether Kalshi's daily temperature markets are
mispriced relative to a probability forecast derived from NOAA's GEFS ensemble.

**This is a research project. It does data, modeling, backtesting, and paper
trading only — no real money, no live order placement, in any phase.** The most
likely honest outcome is "the market is efficient after costs," and that is a
valid result. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the full thesis,
phase plan, and ground rules; [`PLAN.md`](PLAN.md) tracks the current phase.

## Current status

**Phase 6 — calibration harness complete.** GEFS ingestion, observations
ingestion, the bias-corrected fair-value model, the scheduled order-book logger,
the costs-included backtester, and the calibration harness all work. Paper
trading is Phase 7.

The backtester needs logged price history *and* resolved outcomes to line up —
the logger started 2026-05-21, so the first real backtest trades appear once
the earliest logged markets resolve. Until then `run_backtest.py` reports
honestly that there are no completed trades.

Data pipeline so far (each reproducible from one command):

```sh
python scripts/ingest_gefs.py --latest           # GEFS ensemble -> data/ensemble/
python scripts/ingest_observations.py             # observed highs -> data/observations/
python scripts/fetch_kalshi_rules.py              # re-verify Kalshi resolution rules
python scripts/discover_kalshi_series.py           # re-verify active Kalshi series
python scripts/backfill_gefs.py                   # 1yr GEFS history (bias training set)
python scripts/fit_bias.py                        # fit per-station/season bias model
python scripts/compare_fairvalue.py               # fair value vs. live Kalshi markets
python scripts/log_kalshi.py                       # one order-book snapshot pass
python scripts/run_backtest.py                     # costs-included P&L backtest
python scripts/run_calibration.py                  # model calibration report
```

## Scheduling the order-book logger

`scripts/log_kalshi.py` does one polling pass (snapshots every temperature
market's order book into `data/kalshi.db`) and exits. A macOS **launchd** agent
runs it every 15 minutes so history accumulates for the Phase 5 backtest.

```sh
# install / start
cp scripts/com.kalshiweather.logkalshi.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.kalshiweather.logkalshi.plist

launchctl list | grep kalshiweather      # check it is registered
tail -f data/log_kalshi.out.log          # watch pass output

# stop / remove
launchctl unload -w ~/Library/LaunchAgents/com.kalshiweather.logkalshi.plist
```

`kalshi.db` holds two tables: `markets` (one row per market) and
`orderbook_snapshots` (one row per market per poll — best bid/ask, sizes,
`mid`, `spread`, and the full raw order book as JSON). At ~15-minute polling it
grows roughly ~1 GB/month; prune old `raw_orderbook` blobs if it gets large.

## Requirements

- macOS on Apple Silicon (arm64). Developed on a MacBook Pro 2024.
- Python 3.11+
- [Homebrew](https://brew.sh/)
- [`uv`](https://github.com/astral-sh/uv) (preferred) — fast Python package
  manager. Install with `brew install uv`.

## Setup (Apple Silicon)

### 1. Install the ecCodes system library

GEFS data is GRIB-encoded. `cfgrib` (used in Phase 1) needs the **ecCodes**
C library, which is a system dependency — not a pip package. Install it via
Homebrew:

```sh
brew install eccodes
```

This is a known Apple Silicon setup pain point. Phase 1 explicitly verifies
that `cfgrib` finds ecCodes before any modeling is built on top of it. For
Phase 0, ecCodes is not yet exercised.

### 2. Create the virtual environment and install dependencies

From the repo root:

```sh
uv venv --python 3.11           # creates .venv/
source .venv/bin/activate
uv pip install -e ".[dev]"      # runtime + dev (pytest, ruff) dependencies
```

If `cfgrib`/`xarray` installation gives trouble, it does not block Phase 0 —
only Phase 1 depends on them.

### 3. Verify the skeleton

```sh
pytest                  # config-layer smoke tests should pass
python scripts/hello.py # loads config.yaml, prints the 20 configured stations
```

## Configuration

All tunable values live in [`config.yaml`](config.yaml) — stations, GEFS
ingestion settings, Kalshi API settings, strategy thresholds, and storage
paths. Nothing is hardcoded in code. The file is committed to git (it holds no
secrets). Load it via `src.common.config.load_config()`.

### Resolution rules (verified in Phase 2)

Resolution-rule mismatch (wrong NWS station, wrong observation window, wrong
rounding or day-cutoff) is the **#1 source of fake trading edges**. Phase 2
verified every station against Kalshi's *live* contract `settlement_sources`.

**How Kalshi settles temperature markets** (confirmed from live `KXHIGH*`
markets, 2026-05-21):

- Markets settle on the **NWS Climatological Report (Daily)** — the "CLI"
  product — for a single named station. Not weather apps, not raw METARs.
- The CLI daily high is a **whole °F** integer.
- The CLI "day" runs **local midnight-to-midnight standard time** (during
  Daylight Saving Time, 1:00 AM–12:59 AM the next day).
- Each `config.yaml` station's `id` is the NWS CLI station code (`K` + Kalshi's
  `issuedby` code); `resolution_station` / `resolution_notes` record the
  verified settlement source. Re-verify any time with
  `python scripts/fetch_kalshi_rules.py`.

**Correction made:** Kalshi settles **Houston on Hobby Airport (KHOU)**, not
Bush Intercontinental (KIAH). The Phase-1 config had Bush; Phase 2 corrected the
station id and coordinates so GEFS forecasts the station the market settles on.

**Observed highs** are ingested from the same CLI product, via the Iowa
Environmental Mesonet (IEM) CLI archive. By using the CLI `high` directly we
inherit Kalshi's exact day-window definition rather than reconstructing it from
hourly data. Note: Kalshi's own rules warn that *preliminary* CLI values can be
revised — a caveat the backtester and paper-trader must respect.

## Repository layout

```
kalshi-weather/
├── PROJECT_BRIEF.md     # full project brief, thesis, phase plan
├── PLAN.md              # living plan — updated at the start of each phase
├── README.md            # this file
├── config.yaml          # stations, markets, thresholds (single source of truth)
├── pyproject.toml       # dependencies + tooling config
├── data/                # gitignored — local data store (Parquet + SQLite)
├── src/
│   ├── common/          # config loader, storage helpers
│   ├── ingest/          # GEFS / observations / Kalshi ingestion (Phases 1,2,4)
│   ├── model/           # bias correction + fair-value engine (Phase 3)
│   ├── backtest/        # backtester + calibration harness (Phases 5,6)
│   └── papertrade/      # paper trading loop (Phase 7)
├── scripts/             # thin CLI entry points
├── notebooks/           # exploratory analysis
└── tests/               # pytest
```

## Reproducibility

Any result (a backtest, a calibration plot) can be regenerated from a single
documented command. Commands are added to this README as each phase lands.
