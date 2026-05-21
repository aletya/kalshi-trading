# Kalshi Weather Trading Research System

A research system that tests whether Kalshi's daily temperature markets are
mispriced relative to a probability forecast derived from NOAA's GEFS ensemble.

**This is a research project. It does data, modeling, backtesting, and paper
trading only — no real money, no live order placement, in any phase.** The most
likely honest outcome is "the market is efficient after costs," and that is a
valid result. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the full thesis,
phase plan, and ground rules; [`PLAN.md`](PLAN.md) tracks the current phase.

## Current status

**Phase 0 — project skeleton & environment.** The repo is a runnable skeleton:
config layer, dependency manifest, tests, and a smoke script. No GEFS, Kalshi,
or model code yet — those arrive phase by phase.

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

### A note on resolution rules (important)

Each station in `config.yaml` carries `resolution_station` and
`resolution_notes` fields that are **intentionally blank in Phase 0**. The
`id`/`latitude`/`longitude` values are best-known placeholders — good enough to
find a nearby GEFS grid point, but **not** confirmed to match how Kalshi
actually settles each market.

Resolution-rule mismatch (wrong NWS station, wrong observation window, wrong
rounding or day-cutoff) is the **#1 source of fake trading edges**. Phase 2
reads Kalshi's actual contract rules, fills in these fields, and documents every
assumption here. Until then, treat station identity as unverified.

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
