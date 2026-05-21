# PLAN.md — Living Plan

Updated at the start of every phase. Reflects the **current** phase's plan and
the log of completed phases. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the
full brief.

---

## Current status

- **Phase:** 3 — Fair-value engine
- **State:** ✅ Built and verified. 🛑 Awaiting Checkpoint-3 sign-off before
  starting Phase 4.

### Decisions on the Phase 3 open questions
1. **Backfill scope:** 1 year of GEFS history, one run per week (~52 runs,
   ~80 min background job) — every calendar day covered once, ~90 pairs per
   season per station.
2. **Distribution:** Gaussian fit — fit `N(mean, std)` to the 31 bias-corrected
   members; `P(bucket) = Φ(hi) − Φ(lo)`. Phase 6 calibration can revisit.

---

## Phase 3 plan — Fair-value engine

### Goal
Turn a GEFS ensemble slice into a **probability per Kalshi bucket** for a
station's daily high — bias-corrected and calibration-ready. Definition of done:
given a real ensemble slice and a set of buckets, output a probability vector
that sums to ~1; and show fair value next to real Kalshi prices at Checkpoint 3.

### The pipeline (ensemble slice → bucket probabilities)
1. **Per-member daily high.** For each of the 31 members, take the max of its
   3-hourly `temp_2m_f` trace over the **target local day**. The day window
   must match Kalshi's CLI day: local midnight–midnight *standard* time (LST,
   year-round — Kalshi uses LST even during DST). → 31 raw daily-high values.
2. **Bias correction.** Apply a per-station, per-season correction (fit in this
   phase) to the 31 values. This absorbs systematic GEFS error, including the
   slight low bias from sampling a continuous max at 3-hour steps.
3. **Distribution → buckets.** Build a smoothed daily-high distribution from the
   31 bias-corrected members and integrate it over each bucket:
   `P(bucket [lo,hi)) = CDF(hi) − CDF(lo)`.

The ensemble *spread* carries the lead-time-dependent uncertainty naturally
(longer lead → members disagree more → wider distribution). The bias model only
corrects the systematic *level*. Whether the spread is well-calibrated is
measured in Phase 6 — if not, we revisit.

### Prerequisite: a GEFS history backfill
Bias correction needs historical `(forecast, observed)` pairs, and so far we
have only one live GEFS run. Phase 3 therefore starts with a bounded backfill:
ingest one GEFS run per week over the chosen window (each run's 7-day forecast
tiles the calendar), reusing Phase 1's `ingest_run`. Scope is a human decision
(see questions below) — it is a one-time background job.

### Files to create / change

| File | Purpose |
|---|---|
| `src/model/daily_high.py` | Local-day windowing → 31 per-member daily highs from an ensemble slice. Shared by bias fitting and fair value. |
| `src/model/bias.py` | Build training pairs (ensemble daily-highs ⨝ observations), fit per-station/per-season linear regression `observed ≈ a + b·raw`, persist + apply. |
| `src/model/fairvalue.py` | Pure function: `(ensemble slice, station, date, buckets, bias_model) → {bucket: probability}`. |
| `scripts/backfill_gefs.py` | Loop `ingest_run` over historical dates (the backfill). |
| `scripts/fit_bias.py` | Build pairs, fit the bias model, save to `data/model/bias.json`. |
| `scripts/compare_fairvalue.py` | Checkpoint-3 demo: read-only fetch of live Kalshi temp markets, compute fair value, print side-by-side. |
| `tests/test_daily_high.py`, `tests/test_bias.py`, `tests/test_fairvalue.py` | Synthetic-input unit tests with known answers. |

### Data shapes
- **Bias model** — `data/model/bias.json`: per `(station_id, season)` →
  `{a, b, n_pairs, rmse}`.
- **`fairvalue` output** — `dict[Bucket, float]`; a `Bucket` is `(low, high)`
  with `None` for an open end (handles Kalshi's `>X°` threshold markets and
  range buckets alike).

### Key design choices
- **Bias model:** per-station, per-season (4 meteorological seasons) ordinary
  least-squares `observed ≈ a + b·raw_member_mean`; applied to every member.
  Simple and interpretable, per the brief.
- **Known simplification (flagged for Phase 6):** the bias fit is *not*
  conditioned on lead time. The ensemble spread covers lead-dependent
  uncertainty; Phase 6 calibration will reveal whether a lead term is needed.
- **New Orleans (KMSY):** only ~3.5 years of observations (Phase 2 gap). Its
  per-season fits will rest on fewer pairs — `bias.json` records `n_pairs` so
  thin fits are visible.

### Unit tests (synthetic, known-answer)
- `daily_high`: a crafted ensemble where the per-member maxima are known;
  correct local-day windowing incl. the DST/LST offset.
- `bias`: fit on synthetic pairs with a known `a, b`; recover them.
- `fairvalue`: 31 members all = 70 °F → P≈1 in the bucket containing 70;
  Gaussian-distributed members → bucket probabilities match the Gaussian CDF;
  probabilities over a partition sum to ~1.

### Definition of done
`fairvalue` produces a normalized probability vector for real buckets from a
real ensemble slice; `compare_fairvalue.py` shows fair value beside live Kalshi
prices for a few cities.

### Open questions — RESOLVED
1. ~~Backfill scope~~ → 1 year weekly (~52 runs). See Decisions at top.
2. ~~Distribution~~ → Gaussian fit. See Decisions at top.

### Not in Phase 3
No Kalshi order-book logging (Phase 4), no backtest P&L (Phase 5), no
calibration harness (Phase 6).

---

## Phase log

### Phase 3 — built & verified (2026-05-21)

**Decisions:** 1-year weekly GEFS backfill; Gaussian distribution.

- `src/model/daily_high.py`: local-standard-day windowing → per-member highs.
- `src/model/bias.py`: training-pair builder, per-station/per-season OLS, apply.
- `src/model/fairvalue.py`: Gaussian fit → bucket probabilities (pure function).
- `scripts/backfill_gefs.py`, `fit_bias.py`, `compare_fairvalue.py`.
- `tests/test_daily_high.py`, `test_bias.py`, `test_fairvalue.py`: 23 new tests.
- **Verified:** `pytest` → 51 passed. Backfill ingested 53 GEFS runs (110 min,
  0 failed). `fit_bias` → 7,076 pairs, all 80 station-season cells got
  own-season fits, mean RMSE 3.67 °F. `compare_fairvalue` → plausible
  bias-corrected highs (e.g. Phoenix 96.6 °F, NYC 65.2 °F for 2026-05-22).
- **Kalshi quotes:** live quotes come from the **orderbook endpoint**, not the
  markets-list summary (which never populates `yes_bid`/`yes_ask`). With that
  fixed, `compare_fairvalue` shows real prices — spreads are tight (~1-3¢) and
  there are real fair-value disagreements. This shapes Phase 4 (log orderbooks).
- **Known issues (for Phase 6 calibration to judge):** (1) low-variance
  summer/coastal cells (Houston/Miami/SF JJA) have ill-conditioned regression
  slopes — fine inside the training range, risky to extrapolate; (2) ensemble
  dispersion vs. the market needs checking — at +1 day NYC our σ≈3.7 °F looks
  wider than the market implies, while Miami σ≈0.8 °F looks tight.

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
  2022-10-01 (~1,326 days) vs ~2,690 days for the others.
- Committed `5e170b0`, pushed to GitHub.

### Phase 1 — built & verified (2026-05-21)

**Decisions:** lead times 3-hourly to +168 h (`forecast_hours` → `{start,stop,
step}` mapping); grid resolution 0.5° `pgrb2a`.

- `src/ingest/gefs.py`: `.idx` byte-range subsetting (~0.5 MB/message, no raw
  GRIB kept), cfgrib decode, nearest-grid-point sampling, threaded `ingest_run`,
  `--latest` run probe.
- `src/common/storage.py`: Parquet path + write helpers.
- `scripts/ingest_gefs.py`: CLI (`--latest` / `--date`+`--cycle`, subset flags).
- `config.yaml`: `gefs.forecast_hours` → `{start:3, stop:168, step:3}`.
- `tests/test_gefs.py` (+ `tests/fixtures/sample.idx`): 14 offline unit tests.
- **Verified:** `pytest` → 20 passed. Live run pulled GEFS 2026-05-21 12Z —
  1736/1736 member-files, 34,720 rows (20×31×56), 0.26 MB Parquet, 93 s.
  cfgrib + ecCodes confirmed working.
- Committed `56d3751`, pushed to GitHub.

### Phase 0 — built & verified (2026-05-21)

**Decisions:** 20 cities; `config.yaml` committed directly to git; proposed
config field set sufficient.

- Repo skeleton: `pyproject.toml`, `.gitignore`, `config.yaml`,
  `src/common/config.py` (typed validated loader), `scripts/hello.py`,
  `tests/test_smoke.py`, `README.md`.
- `uv` env on CPython 3.11.0; deps installed incl. cfgrib/xarray.
- **Verified:** `pytest` → 6 passed; `hello.py` prints all 20 stations.
- Committed `4a8c653`; GitHub repo created (public) and pushed.
