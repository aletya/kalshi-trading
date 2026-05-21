# PLAN.md — Living Plan

Updated at the start of every phase. Reflects the **current** phase's plan and
the log of completed phases. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the
full brief.

---

## Current status

- **Phase:** 2 — Observations ingestion (ground truth)
- **State:** ✅ Built and verified. 🛑 Awaiting Checkpoint-2 sign-off before
  starting Phase 3.

---

## Phase 2 plan — Observations ingestion (ground truth)

### Goal
Pull historical observed daily high temperatures for all 20 stations **from the
exact source Kalshi settles on**, and write a Parquet table of
`(station, date, observed_high)`. Also: read each city's *live* Kalshi contract
rules and lock the resolution details into `config.yaml`. The brief calls
resolution-rule mismatch the **#1 source of fake edges** — this phase exists to
shut that trap.

### What we confirmed about Kalshi settlement (research done while planning)
- Kalshi temperature markets settle on the **NWS Climatological Report (Daily)**
  — the "CLI" product — for a **specific named station**. Verified from the live
  `KXHIGHNY` market rules: *"the highest temperature recorded in Central Park,
  New York … as reported by the National Weather Service's Climatological Report
  (Daily)."*
- Day window: midnight–midnight **local standard time** (during DST the CLI day
  runs 1:00 AM–12:59 AM the next day). The CLI report already encodes this — by
  ingesting the CLI `high` we inherit Kalshi's exact day definition for free.
- Kalshi's secondary rules explicitly warn that *preliminary* CLI data is
  subject to rounding/conversion nuances and later revision.

### Data source — decided
- **IEM (Iowa Environmental Mesonet) CLI archive:**
  `https://mesonet.agron.iastate.edu/json/cli.py?station=<ID>&fmt=json` —
  confirmed to return parsed CLI records with a Fahrenheit `high` field plus
  `high_time`. This is the *same* NWS CLI product Kalshi settles on, so it is
  the faithful historical source. (Phase 2 verifies the exact date-range query
  params, the way Phase 1 verified cfgrib.)
- `api.weather.gov` (the brief's suggestion) is reserved for **Phase 7**
  settlement-day pulls — it serves recent CLI products but not deep history.
- GHCN-Daily TMAX was considered as a cross-check and **dropped**: it would only
  measure GHCN's agreement with CLI, not validate CLI. CLI *is* the settlement
  ground truth by definition.

### Files to create / change

| File | Purpose |
|---|---|
| `src/ingest/observations.py` | Fetch + normalize CLI daily highs |
| `scripts/ingest_observations.py` | CLI entry point for the backfill |
| `scripts/fetch_kalshi_rules.py` | Read-only research helper: pulls live Kalshi temp-market rules for all 20 cities, prints the settlement station/source, flags config mismatches. **Self-contained — not the Phase 4 logging system.** |
| `tests/test_observations.py` (+ `tests/fixtures/sample_cli.json`) | Offline unit tests |
| `config.yaml` | Fill in `resolution_station` / `resolution_notes` |
| `README.md` | Document the resolution assumptions explicitly |

### `observations.py` — key functions
- `fetch_cli(client, station, start, end) -> list[dict]` — GET IEM `cli.py`.
- `normalize_cli_records(raw, station_id) -> list[ObsRow]` — extract
  `(station_id, date, observed_high_f, high_time, source, retrieved_at)`; drop
  records with a missing/null high.
- `ingest_observations(stations, start, end, out_path) -> IngestResult` —
  loop stations, write one combined Parquet table.

### Output — `data/observations/observed_highs.parquet`

| column | type | notes |
|---|---|---|
| `station_id` | str | config station id |
| `date` | date | local climate date |
| `observed_high_f` | float | CLI daily high, °F |
| `high_time` | str | time the high was recorded (diagnostic) |
| `source` | str | `"IEM-CLI"` |
| `retrieved_at` | datetime (UTC) | fetch timestamp |

### CLI
```
python scripts/ingest_observations.py [--stations KNYC,...] [--start 2019-01-01] [--end today]
python scripts/fetch_kalshi_rules.py     # per-city settlement station + config mismatch report
```

### The resolution reconciliation (core of Checkpoint 2)
For each of the 20 cities, `fetch_kalshi_rules.py` extracts the exact settlement
station from the live Kalshi market. We then:
1. Fill `resolution_station` + `resolution_notes` in `config.yaml`.
2. Confirm the CLI station id queried at IEM matches it.
3. Flag any city where Kalshi's station differs from the airport our Phase-1
   GEFS coordinates point at (e.g. if Kalshi Chicago settles somewhere other
   than Midway) — and correct the config coords so GEFS samples the right place.

### Tests (offline)
- CLI JSON parsing from a fixture.
- Records with null/missing `high` are dropped.
- Date parsing / °F handling.
- Config station-mismatch detection logic.

### Definition of done
`python scripts/ingest_observations.py` produces `observed_highs.parquet` with
`(station, date, observed_high)` rows for all 20 stations; `config.yaml`
resolution fields populated; assumptions documented in `README.md`.

### Open question for the human (Checkpoint 2)
- **History depth.** Default backfill start is **2019-01-01** (~7 years).
  Observations are tiny (JSON/text), so depth is cheap; the real constraint on
  bias correction is GEFS reforecast availability (a Phase 3 concern). Easy to
  change via `--start`.

### Not in Phase 2
No bias correction, no GEFS pairing, no fair value (Phase 3). No Kalshi
order-book logging (Phase 4).

---

## Phase log

### Phase 2 — built & verified (2026-05-21)

**Decisions:** observation source = NWS CLI via IEM archive (the exact Kalshi
settlement source); GHCN-Daily dropped; backfill from 2019-01-01.

- `src/ingest/observations.py`: IEM CLI fetch, normalization (drops missing
  highs), date-range filter, `ingest_observations`.
- `scripts/ingest_observations.py`: CLI backfill.
- `scripts/fetch_kalshi_rules.py`: read-only Kalshi resolution-rules verifier.
- `tests/test_observations.py` (+ `sample_cli.json`): 8 offline unit tests.
- `config.yaml`: all 20 `resolution_station`/`resolution_notes` filled from live
  Kalshi `settlement_sources`. **Houston corrected KIAH → KHOU** (Kalshi settles
  Houston on Hobby, not Bush Intercontinental) — id + coords updated.
- `README.md`: resolution rules documented.
- **Verified:** `pytest` → 28 passed. `fetch_kalshi_rules.py` → all 20 stations
  reconcile with Kalshi CLI sites. `ingest_observations.py` → 52,506 rows
  (20 stations, 2019-01-01 → 2026-05-21), 0.34 MB Parquet.
- **Known data gap:** New Orleans (KMSY) CLI archive in IEM only starts
  2022-10-01 (~1,326 days) vs ~2,690 days for the others — fewer years for bias
  correction. Flagged for Phase 3.

### Phase 1 — built & verified (2026-05-21)

**Decisions:** lead times 3-hourly to +168 h (`forecast_hours` → `{start,stop,
step}` mapping); grid resolution 0.5° `pgrb2a`.

- `src/ingest/gefs.py`: `.idx` byte-range subsetting (~0.5 MB/message, no raw
  GRIB kept), cfgrib decode, nearest-grid-point sampling, threaded `ingest_run`,
  `--latest` run probe.
- `src/common/storage.py`: Parquet path + write helpers.
- `scripts/ingest_gefs.py`: CLI (`--latest` / `--date`+`--cycle`, subset flags).
- `config.yaml`: `gefs.forecast_hours` → `{start:3, stop:168, step:3}`; loader
  expands it and still accepts a plain list.
- `tests/test_gefs.py` (+ `tests/fixtures/sample.idx`): 14 offline unit tests.
- **Verified:** `pytest` → 20 passed. Live run pulled GEFS 2026-05-21 12Z —
  1736/1736 member-files, 34,720 rows (20×31×56), 0.26 MB Parquet, 93 s.
  cfgrib + ecCodes confirmed working.
- Committed `56d3751`, pushed to GitHub.

### Phase 0 — built & verified (2026-05-21)

**Decisions:** 20 cities; `config.yaml` committed directly to git; proposed
config field set sufficient.

- Repo skeleton: `pyproject.toml`, `.gitignore`, `config.yaml` (20 stations,
  resolution fields blank), `src/common/config.py` (typed validated loader),
  `scripts/hello.py`, `tests/test_smoke.py`, `README.md`.
- `uv` env on CPython 3.11.0; deps installed incl. cfgrib/xarray.
- **Verified:** `pytest` → 6 passed; `hello.py` prints all 20 stations.
- Committed `4a8c653`; GitHub repo created (public) and pushed.
