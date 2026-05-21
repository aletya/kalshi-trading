# PLAN.md — Living Plan

This document is updated at the start of every phase. It always reflects the
**current** phase's plan and awaits human approval before code is written.

---

## Current status

- **Phase:** 0 — Project skeleton & environment
- **State:** ✅ Built and verified. 🛑 Awaiting Checkpoint-0 sign-off before
  starting Phase 1.

### Decisions from Checkpoint 0

1. **Stations — 20 cities** (all daily high-temp markets the human wants to
   track): Atlanta, Austin, Boston, Chicago, Dallas, Denver, Houston, Las Vegas,
   Los Angeles, Miami, Minneapolis, New York City, Oklahoma City, Philadelphia,
   San Antonio, San Francisco, Seattle, Phoenix, New Orleans, Washington DC.
   - All 20 go into `config.yaml`. Each gets a placeholder ICAO/airport station
     id + approximate coords; `resolution_station` and `resolution_notes` stay
     **blank**, to be verified against Kalshi contract rules in Phase 2.
   - Phase 1 GEFS ingestion will accept a `--stations` subset arg so we can
     start with a few stations while validating cfgrib, then scale to all 20.
2. **`config.yaml` committed directly to git** (no secrets in it).
3. **Config field set as proposed is sufficient** — extend per-phase as needed.

---

## Phase 0 plan — Project skeleton & environment

### Goal
Stand up an empty-but-runnable repository: directory structure, dependency
manifest, virtual environment, gitignore, pytest wired up, and a documented
`config.yaml`. No domain logic. Definition of done: `pytest` runs (with zero or
one trivial test), the env activates, and a hello-style script runs.

### Files to create

| File | Purpose |
|---|---|
| `.gitignore` | Ignore `data/`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, notebook checkpoints, `.DS_Store` |
| `pyproject.toml` | Project metadata + dependencies (see below); pytest config |
| `README.md` | Apple Silicon setup: Homebrew `eccodes`, `uv` install, env creation, how to run |
| `config.yaml` | Documented config (fields below) |
| `config.example.yaml` | Optional — committed template if `config.yaml` itself ends up gitignored (decision point below) |
| `src/__init__.py` + package `__init__.py` files | Make `src` importable as packages |
| `src/common/config.py` | Loads + validates `config.yaml` into a typed object |
| `scripts/hello.py` | Hello-style smoke script: loads config, prints stations, confirms env |
| `tests/test_smoke.py` | One trivial test: imports `src.common.config`, loads `config.yaml`, asserts it parses |
| Empty dirs w/ `.gitkeep` | `data/ensemble/`, `data/observations/`, `notebooks/`, plus stub package dirs |

### Directory structure created in Phase 0

```
kalshi-weather/
├── .gitignore
├── PROJECT_BRIEF.md        ✓ already created
├── PLAN.md                 ✓ this file
├── README.md
├── config.yaml
├── pyproject.toml
├── data/                   (gitignored; .gitkeep in subdirs)
│   ├── ensemble/
│   └── observations/
├── src/
│   ├── __init__.py
│   ├── common/
│   │   ├── __init__.py
│   │   └── config.py
│   ├── ingest/__init__.py
│   ├── model/__init__.py
│   ├── backtest/__init__.py
│   └── papertrade/__init__.py
├── scripts/
│   └── hello.py
├── notebooks/              (.gitkeep)
└── tests/
    └── test_smoke.py
```

Only the skeleton is created now. The actual modules (`gefs.py`,
`fairvalue.py`, etc.) arrive in their own phases.

### Dependencies (declared in `pyproject.toml`, not all used until later phases)

- Runtime: `httpx`, `polars`, `pandas`, `pyyaml`, `xarray`, `cfgrib`,
  `pyarrow`, `numpy`, `boto3` (or anonymous S3 access for NOAA buckets)
- Dev: `pytest`, `ruff` (lint/format)

Note: `cfgrib` needs the system ecCodes library — installed via Homebrew, not
pip. The README documents this; Phase 1 verifies it actually works.

### Proposed `config.yaml` fields (the main thing to review at Checkpoint 0)

```yaml
# --- Stations: the weather stations we forecast & trade ---
stations:
  - id: KNYC                 # NWS/ICAO station identifier
    name: New York City (Central Park)
    kalshi_city: NYC         # how Kalshi labels this city in market tickers
    latitude: 40.7790
    longitude: -73.9693
    timezone: America/New_York
    # Resolution detail — FILLED IN / VERIFIED in Phase 2 against Kalshi rules:
    resolution_station: ""   # exact station Kalshi settles against
    resolution_notes: ""     # observation window, rounding, day-cutoff

# --- GEFS ensemble ingestion ---
gefs:
  s3_bucket: noaa-gefs-pds
  model_runs: ["00", "06", "12", "18"]   # which cycles to pull
  members: 31                            # ensemble size
  forecast_hours: [24, 48, 72, 96, 120]  # lead times to extract
  variable: "2t"                         # 2-metre temperature

# --- Kalshi market ingestion ---
kalshi:
  api_base: "https://api.elections.kalshi.com/trade-api/v2"
  market_series: []          # temperature market series tickers to track
  poll_interval_minutes: 15  # order-book snapshot cadence

# --- Strategy thresholds ---
strategy:
  min_edge: 0.05             # min (our prob - market price) to consider a trade
  max_position_per_market: 100   # paper-trading sizing cap (contracts)
  require_edge_exceeds_spread: true

# --- Storage paths ---
paths:
  data_dir: "data"
  ensemble_dir: "data/ensemble"
  observations_dir: "data/observations"
  database: "data/kalshi.db"
```

### Open questions — RESOLVED at Checkpoint 0

1. ~~Stations~~ → 20 cities (see Decisions above).
2. ~~`config.yaml` in git~~ → committed directly.
3. **`pandas` and `polars`** → include both; polars for logs, pandas with xarray.
4. ~~Config field coverage~~ → proposed set is sufficient.

### Definition of done for Phase 0

- `git init` done; `.gitignore` excludes `data/` and the venv.
- `uv` virtual env created and activates.
- `pip`/`uv` installs the dev dependencies; `pytest` runs and passes the smoke test.
- `python scripts/hello.py` loads `config.yaml` and prints the configured stations.

### Not in Phase 0

No GEFS, no Kalshi API, no model, no backtester. Those are Phases 1–7.

---

## Phase log

### Phase 0 — built & verified (2026-05-21)

- Repo initialized (`git init`); `.gitignore` excludes `data/` and `.venv/`.
- `pyproject.toml` declares runtime + dev deps and pytest/ruff config.
- `config.yaml` written with all 20 stations (resolution fields blank).
- `src/common/config.py` — typed, validated config loader.
- `scripts/hello.py` smoke script + `tests/test_smoke.py` (6 tests).
- `uv` installed via Homebrew; `.venv` created (CPython 3.11.0);
  `uv pip install -e ".[dev]"` succeeded — incl. `cfgrib`/`xarray` (runtime
  ecCodes link still to be verified in Phase 1).
- **Verified:** `pytest` → 6 passed; `python scripts/hello.py` → prints all
  20 stations and config summary.
- Files staged in git but **not committed** (awaiting user go-ahead).
