# PLAN.md — Living Plan

Updated at the start of every phase. Reflects the **current** phase's plan and
the log of completed phases. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the
full brief.

---

## Current status

- **Phase:** 6 — Calibration harness
- **State:** ✅ Built and verified. 🛑 Awaiting Checkpoint-6 sign-off before
  starting Phase 7.

### Decision on the Phase 6 open question
- **OOS rigour:** report raw-ensemble calibration (clean, no fitting) +
  bias-corrected calibration labelled **in-sample**. No time-split run — the
  genuine out-of-sample verdict is Phase 7 paper trading.

---

## Phase 6 plan — Calibration harness

### Goal
Measure whether the fair-value model is **calibrated**: when it says 30%, does
the event happen ~30% of the time? Bin predictions by predicted probability,
compare to realized frequency, and report a reliability table + Brier score.
Definition of done: a calibration report over the prediction set.

### Why this can run on real data *now*
Calibration needs `(predicted probability, realized outcome)` pairs — **not**
Kalshi prices. We already have everything: the 53 backfilled GEFS runs and the
observed CLI highs. So unlike Phase 5, Phase 6 produces a real result
immediately.

### The prediction set
For each backfilled GEFS run × station × target day it covers (lead 0–6 d),
fit the fair-value Gaussian and emit threshold predictions: for a spread of
integer-°F thresholds `T` around the forecast (covering ~±2σ so all probability
levels are exercised), the pair `(P(high > T), 1[observed > T])` with a ±0.5 °F
continuity correction. This calibrates the forecast CDF — if `P(high > T)` is
calibrated, so are the Kalshi bucket probabilities (differences of the CDF).

### Raw vs. bias-corrected (honesty)
The bias model was fit on these same backfilled runs, so its calibration here
is **in-sample** (optimistic). We therefore report **two** calibration runs:
- **Raw ensemble** (no bias correction) — fully independent of any fitting; the
  clean read on whether the ensemble's *dispersion* is right (the Phase 3
  concern: under-/over-dispersion).
- **Bias-corrected** — labelled in-sample; the genuine out-of-sample verdict is
  Phase 7 paper trading.

(See open question: whether to also add a time-split out-of-sample run.)

### Files to create
| File | Purpose |
|---|---|
| `src/backtest/calibration.py` | Build predictions, reliability table, Brier score, by-lead breakdown. |
| `scripts/run_calibration.py` | CLI → reliability table + ASCII reliability diagram + Brier, raw vs. bias-corrected. |
| `tests/test_calibration.py` | Synthetic known-answer tests. |

### `calibration.py` — key functions
- `build_predictions(ensemble_dir, observations, stations, bias_model) -> list[Prediction]`
  — `Prediction(lead_days, predicted_prob, outcome)`.
- `reliability_table(predictions, n_bins=10) -> list[Bin]` — per decile:
  `count`, `mean_predicted`, `observed_frequency`.
- `brier_score(predictions) -> float`.
- `by_lead(predictions) -> dict[lead → (brier, n)]` — reveals lead-dependent
  dispersion problems.

### Report contents
- Reliability table (10 bins): predicted vs. realized frequency, counts.
- An ASCII reliability diagram (predicted on one axis, realized on the other).
- Overall Brier score; Brier by lead-time bucket (1-2 d / 3-4 d / 5-7 d).
- Run twice — raw and bias-corrected — side by side.
- A plain-English verdict: well calibrated, over-confident (under-dispersed),
  or under-confident. If poor, the model is the problem — iterate Phase 3.

### Tests (synthetic, known-answer)
- A perfectly calibrated synthetic set → reliability table on the diagonal,
  Brier matches the analytic value.
- A deliberately over-confident set → reliability table bows off the diagonal
  in the known direction.
- `reliability_table` bin edges / empty-bin handling.

### Open question — RESOLVED
1. ~~Bias-corrected calibration rigour~~ → in-sample (labelled) + raw; no
   time-split. See Decision at top.

### Not in Phase 6
No live paper trading (Phase 7). No model changes — Phase 6 *measures*; any
fix to the bias model or ensemble spread is decided at Checkpoint 6.

---

## Phase log

### Phase 6 — built & verified (2026-05-22)

**Decision:** raw + in-sample bias-corrected calibration; no time-split.

- `src/backtest/calibration.py`: threshold-prediction builder, reliability
  table, Brier score, calibration slope, by-lead breakdown.
- `scripts/run_calibration.py`: report + ASCII reliability diagram.
- `tests/test_calibration.py`: 13 synthetic known-answer tests.
- **Verified:** `pytest` → 80 passed. Real calibration result over ~57k
  predictions:
  - **Raw ensemble:** over-confident / under-dispersed — calibration slope
    **0.77**, Brier **0.19**. Confirms the Phase 3 concern.
  - **Bias-corrected (in-sample):** slope **0.88** ("reasonably calibrated"),
    Brier **0.15** — the bias correction substantially helps. Mild residual
    over-confidence remains at the extremes.

### Phase 5 — built & verified (2026-05-21)

**Decision:** trade entry = first qualifying edge, no lookahead.

- `src/backtest/engine.py`: market↔GEFS↔observations join, first-qualifying-edge
  walk, settlement, per-contract P&L executed at the quote + mid-to-mid
  counterfactual.
- `scripts/run_backtest.py`; `tests/test_engine.py` (11 tests, incl. synthetic
  end-to-end).
- **Verified:** `pytest` → 70 passed. `run_backtest` → 0 completed trades yet
  (222 unresolved, 18 no_decision_window) — expected; logger started today.
- Committed `4f5892f`, pushed to GitHub.

### Phase 4 — built & verified (2026-05-21)

**Decisions:** top-of-book + raw-book JSON blob per snapshot; launchd job now.

- `src/ingest/kalshi.py`, `scripts/log_kalshi.py`,
  `scripts/discover_kalshi_series.py`; `tests/test_kalshi.py` (9 tests).
- `config.yaml`: `kalshi_series` per station. launchd agent every 15 min.
- **Verified:** `pytest` → 60 passed; 720 snapshots logged, 0 errors.
- Committed `7e8b860`, pushed to GitHub.

### Phase 3 — built & verified (2026-05-21)

**Decisions:** 1-year weekly GEFS backfill; Gaussian distribution.

- `src/model/daily_high.py`, `bias.py`, `fairvalue.py`; `scripts/backfill_gefs.py`,
  `fit_bias.py`, `compare_fairvalue.py`; 23 tests.
- **Verified:** `pytest` → 51 passed; 53 GEFS runs; 7,076 bias pairs, mean
  RMSE 3.67 °F.
- **Known issues (for Phase 6):** ill-conditioned bias slopes on low-variance
  summer cells; ensemble dispersion vs. the market needs checking.
- Committed `2a9fe01`, pushed to GitHub.

### Phase 2 — built & verified (2026-05-21)

- `src/ingest/observations.py` + scripts; 8 tests. `config.yaml` resolution
  filled; **Houston corrected KIAH → KHOU**. 52,506 observation rows.
- Committed `5e170b0`, pushed to GitHub.

### Phase 1 — built & verified (2026-05-21)

- `src/ingest/gefs.py` (`.idx` byte-range, cfgrib) + script; 14 tests.
  GEFS 2026-05-21 12Z, 34,720 rows. Committed `56d3751`.

### Phase 0 — built & verified (2026-05-21)

- Repo skeleton, `src/common/config.py`, 6 tests. 20 cities. Committed `4a8c653`;
  public GitHub repo created.
