# PLAN.md — Living Plan

This document is updated at the start of every phase. It always reflects the
**current** phase's plan and awaits human approval before code is written.

---

## Current status

- **Phase:** 1 — GEFS ingestion
- **State:** ✅ Built and verified. 🛑 Awaiting Checkpoint-1 sign-off before
  starting Phase 2.

### Decisions on the Phase 1 open questions

1. **Lead times:** 3-hourly out to +168 h (7 days) → 56 steps (3, 6, … 168).
   `config.yaml` `gefs.forecast_hours` changes from an explicit list to a
   `{start, stop, step}` mapping (the loader expands it; downstream code still
   sees a tuple of ints). The loader will also still accept a plain list.
2. **Grid resolution:** 0.5° `pgrb2a` (`pgrb2ap5`, `0p50` files).

---

## Phase 1 plan — GEFS ingestion

### Goal
Given a date, a model-run cycle (00/06/12/18Z), and a list of stations,
download GEFS data from the public `noaa-gefs-pds` S3 bucket, extract 2-metre
temperature for **all 31 ensemble members** at the nearest grid point to each
station, and write tidy Parquet slices. Prove `cfgrib` + ecCodes works on this
machine. **Definition of done:** one command pulls a real recent GEFS run and
produces a Parquet file with `(station, member, valid_time, temp_2m)` rows.

### How GEFS data is laid out (and how we fetch it efficiently)

The `noaa-gefs-pds` bucket stores one GRIB2 file per member per lead time:

```
noaa-gefs-pds/gefs.YYYYMMDD/HH/atmos/pgrb2ap5/
    gec00.tHHz.pgrb2a.0p50.fXXX      # control member
    gep01.tHHz.pgrb2a.0p50.fXXX      # perturbed member 01
    ... gep30 ...                    # 31 members total
```

Each GRIB file holds *many* variables. Downloading whole files would be
hundreds of MB per run and would violate "no raw GRIB hoarding." Instead we use
the standard **`.idx` byte-range** technique:

1. Every GRIB file has a sibling `.idx` text file listing each message with its
   byte offset.
2. Parse the `.idx`, find the line for `TMP : 2 m above ground`.
3. Issue an HTTP `Range` request for just that message (~0.5 MB, not ~50 MB).
4. Decode that one message with `cfgrib`/`xarray`, sample the station points.
5. Discard the raw bytes; keep only the extracted Parquet rows.

S3 access is anonymous over plain HTTPS (`https://noaa-gefs-pds.s3.amazonaws.com/…`)
via `httpx` with `Range` headers — no AWS account, no `boto3` needed.

### Files to create

| File | Purpose |
|---|---|
| `src/ingest/gefs.py` | The ingestion module (functions below) |
| `src/common/storage.py` | Minimal Parquet write/path helpers (ensemble slices) |
| `scripts/ingest_gefs.py` | Thin CLI entry point |
| `tests/test_gefs.py` | Unit tests for the pure logic (no network) |
| `tests/fixtures/sample.idx` | A small real `.idx` snippet for parser tests |

### `src/ingest/gefs.py` — key functions

- `grib_key(date, cycle, member, fhour) -> str` — build the S3 object key.
- `parse_idx(text) -> list[IdxEntry]` — parse a `.idx` file into records
  (`msg_num, start_byte, variable, level, ...`).
- `find_temp_byterange(entries) -> (start, end|None)` — locate the
  `TMP : 2 m above ground` message; `end=None` means "to EOF".
- `fetch_idx(client, key) -> list[IdxEntry]` — GET the `.idx` file.
- `download_message(client, key, byterange) -> bytes` — ranged GET.
- `extract_station_temps(grib_bytes, stations) -> list[dict]` — open the
  message with `xarray.open_dataset(..., engine="cfgrib")`, select each
  station's nearest grid point, return rows. **This is the step that proves
  cfgrib + ecCodes work.**
- `nearest_grid_point(...)` — handles the 0–360° longitude convention
  (station lons are negative; GEFS longitudes run 0…359.5). `xarray`'s
  `.sel(method="nearest")` does the actual snap.
- `ingest_run(date, cycle, stations, forecast_hours, members, out_dir)` —
  orchestrates all members × lead times; writes one Parquet file per run.

### Output data shape (one row per station × member × lead time)

| column | type | notes |
|---|---|---|
| `station_id` | str | e.g. `KNYC` |
| `member` | str | `gec00`, `gep01` … `gep30` |
| `init_time` | datetime (UTC) | model-run timestamp |
| `cycle` | str | `00`/`06`/`12`/`18` |
| `forecast_hour` | int | lead time in hours |
| `valid_time` | datetime (UTC) | `init_time + forecast_hour` |
| `temp_2m_k` | float | raw 2 m temperature, Kelvin (source of truth) |
| `temp_2m_f` | float | Fahrenheit (convenience; Kalshi markets are °F) |
| `grid_latitude` | float | actual GEFS grid point sampled |
| `grid_longitude` | float | actual GEFS grid point sampled |

Written to `data/ensemble/<YYYYMMDD>/gefs_<YYYYMMDD>_<HH>z.parquet`
(partitioned by init date, per the brief).

### `scripts/ingest_gefs.py` CLI

```
python scripts/ingest_gefs.py --date 2026-05-20 --cycle 00
    [--stations KNYC,KORD]        # subset for fast iteration
    [--forecast-hours 24,48]      # subset override
    [--latest]                   # auto-pick most recent posted run
```

### Edge cases handled
- **Missing run** (cycle not posted yet): clear error, suggest `--latest` or an
  earlier cycle.
- **Partial data** (some member files absent): log a warning, write what was
  retrieved, report the count of missing members.
- **`.idx` missing or message not found:** skip that file with a warning.

### Tests (`tests/test_gefs.py`, no network)
- `parse_idx` correctly parses the fixture `.idx`.
- `find_temp_byterange` returns the right offsets (incl. the EOF/last-message case).
- `grib_key` builds the expected S3 key.
- `nearest_grid_point` longitude wrapping: a negative station lon maps into
  0–360°.
- Kelvin→Fahrenheit conversion is correct.

The live "does cfgrib actually work" check is the CLI run itself — shown at
Checkpoint 1.

### Open questions — RESOLVED

1. ~~Lead-time density~~ → 3-hourly to +168 h (see Decisions at top).
2. ~~Grid resolution~~ → 0.5° `pgrb2a`.

### Definition of done for Phase 1
`python scripts/ingest_gefs.py --latest` (or an explicit recent date/cycle)
downloads a real GEFS run and writes a Parquet file with the columns above,
covering all 31 members for the configured stations and lead times.

### Not in Phase 1
No bias correction, no daily-max computation, no bucket probabilities — those
are Phase 3. Phase 1 extracts raw instantaneous 2 m temperatures only.

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

### Phase 1 — built & verified (2026-05-21)

- `config.yaml`: `gefs.forecast_hours` switched to `{start:3, stop:168, step:3}`;
  loader (`config.py`) expands it and still accepts a plain list.
- `src/common/storage.py`: Parquet path + write helpers.
- `src/ingest/gefs.py`: `.idx` parse, byte-range message download, cfgrib
  decode + nearest-grid-point sampling, threaded `ingest_run`, `--latest` probe.
- `scripts/ingest_gefs.py`: CLI with `--latest` / `--date`+`--cycle` /
  `--stations` / `--forecast-hours` / `--members` subsets.
- `tests/test_gefs.py` (+ `tests/fixtures/sample.idx`): 14 offline unit tests.
- **Verified:** `pytest` → 20 passed. Live full run
  `python scripts/ingest_gefs.py --latest` pulled GEFS 2026-05-21 12Z —
  **1736/1736 member-files**, 34,720 rows (20 stations × 31 members × 56 lead
  times), 0.26 MB Parquet, 93 s. cfgrib + ecCodes confirmed working.
- Ensemble values sane: NYC +24h spread 52–61 °F; Phoenix per-member daily-high
  ~88–94 °F.

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
