# PLAN.md — Living Plan

Updated at the start of every phase. Reflects the **current** phase's plan and
the log of completed phases. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the
full brief.

---

## Current status

- **Phase:** 5 — Backtester
- **State:** ✅ Built and verified. 🛑 Awaiting Checkpoint-5 sign-off before
  starting Phase 6.

### Decision on the Phase 5 open question
- **Trade entry:** first qualifying edge — walk a market's snapshots
  chronologically, enter the first time `edge ≥ min_edge` (using only the GEFS
  run available at that timestamp — no lookahead), hold to resolution.

---

## Phase 5 plan — Backtester

### Goal
`src/backtest/engine.py` replays GEFS-derived fair values against the logged
Kalshi order-book snapshots, decides the trade the hold-to-resolution strategy
would take, settles it against the observed high, and reports per-trade and
aggregate **P&L with the bid-ask spread subtracted from every figure**.
Definition of done: a command runs over the accumulated logs and prints a P&L +
hit-rate report, costs included.

### Honest note on data availability (important)
The order-book logger started **today (2026-05-21)**. A backtest trade needs
three things to line up: a logged Kalshi quote, a GEFS fair value at that time,
and the *resolved* observed high. So:
- Right now the real dataset is **near-empty** — no logged market has resolved yet.
- The first real trades appear **~2026-05-23** (when 2026-05-22 markets resolve
  and their CLI highs publish), then grow daily.
- A *meaningful* verdict needs weeks of accumulation — which overlaps with
  Phase 7 paper trading.

So Phase 5 delivers the **engine + report**, fully unit-tested on synthetic
known-answer data, and runs on whatever real data exists (initially tiny). The
real Checkpoint-5 verdict is revisited as the logs fill in. (Phase 6
calibration, by contrast, can run immediately on the 53 backfilled GEFS runs.)

### How a backtest trade is formed
For each logged market (joined: `markets` + `orderbook_snapshots` + GEFS +
observations):
1. **Bucket** — from the market's strike (`greater`/`less`/`between`) with the
   ±0.5 °F continuity correction (as in `compare_fairvalue.py`).
2. **Decision** — walk the market's snapshots in time order. At each, take the
   most recent GEFS run *available at that timestamp* (no lookahead), compute
   the bias-corrected fair-value probability for the bucket.
3. **Trade rule** — buy YES at the ask if `our_P − ask ≥ min_edge`; buy NO at
   `1 − bid` if `bid − our_P ≥ min_edge`. Requires a two-sided quote and
   `require_edge_exceeds_spread`. Entry timing: see open question.
4. **Settle** — the observed CLI high for the target date decides Yes/No.
5. **P&L per contract** — `payoff − price_paid`, where `price_paid` is the
   **ask** (or `1 − bid` for NO). Because we execute at the quote we'd really
   hit, the spread is paid, not hidden. The report also shows the
   mid-to-mid counterfactual to make the spread's drag explicit.

### Files to create / change
| File | Purpose |
|---|---|
| `src/backtest/engine.py` | Join logs+GEFS+obs, form trades, settle, P&L. |
| `scripts/run_backtest.py` | CLI → per-trade + aggregate report. |
| `tests/test_engine.py` | Synthetic known-answer tests (esp. the P&L math). |

(`src/backtest/calibration.py` is Phase 6.)

### Report contents
- **Per trade:** market, station, target date, bucket, our P, bid/ask, side,
  price paid, outcome, P&L.
- **Aggregate:** #trades, hit rate, total & mean P&L per contract (net of
  spread), the mid-to-mid counterfactual and the spread cost it implies, mean
  edge taken, breakdown by city. Explicit "no edge after costs" if that's the
  result.

### GEFS coverage
The backtest pairs each market with the most recent qualifying GEFS run in
`data/ensemble/`; `run_backtest.py` pulls any missing recent run on demand
(`noaa-gefs-pds` retains history), so no extra scheduled job is needed.

### Tests (synthetic, known-answer)
- P&L math: buy YES that wins / loses; buy NO; verify the spread is subtracted
  (execution at ask, never mid).
- Trade rule: edge below threshold → no trade; one-sided quote → no trade.
- Settlement: observed high inside/outside a `between` bucket; `greater`/`less`.

### Definition of done
`python scripts/run_backtest.py` runs over the accumulated logs and prints a
costs-included P&L + hit-rate report (initially near-empty; grows with the logs).

### Open question — RESOLVED
1. ~~Trade entry timing~~ → first qualifying edge (see Decision at top).

### Not in Phase 5
No calibration harness (Phase 6), no live paper trading (Phase 7).

---

## Phase log

### Phase 5 — built & verified (2026-05-21)

**Decision:** trade entry = first qualifying edge, no lookahead.

- `src/backtest/engine.py`: market↔GEFS↔observations join, first-qualifying-edge
  walk (only GEFS available at the snapshot; only snapshots before the target
  local day), settlement on the observed high, per-contract P&L executed at the
  quote (spread paid) + mid-to-mid counterfactual.
- `scripts/run_backtest.py`: per-trade + aggregate costs-included report.
- `tests/test_engine.py`: 11 tests incl. a synthetic on-disk end-to-end run.
- **Verified:** `pytest` → 70 passed. `run_backtest` runs cleanly: **0 completed
  trades yet** — 222 markets `unresolved` (no observation yet), 18
  `no_decision_window` (markets logged only after their day had passed). This
  is the expected data reality — the logger started today; first real trades
  appear ~2026-05-23. Engine + P&L verified on synthetic data.

### Phase 4 — built & verified (2026-05-21)

**Decisions:** top-of-book columns + full raw order book JSON blob per snapshot;
launchd job installed now.

- `src/ingest/kalshi.py`: orderbook parsing, throttled REST fetch with 429
  backoff, SQLite schema + `log_once` polling pass.
- `scripts/log_kalshi.py`, `scripts/discover_kalshi_series.py`.
- `config.yaml`: `kalshi_series` per station; dead `kalshi.market_series` removed.
- `tests/test_kalshi.py`: 9 offline tests.
- launchd agent `com.kalshiweather.logkalshi` — runs every 15 min.
- **Verified:** `pytest` → 60 passed. 3 passes (2 manual + 1 launchd) → 720
  snapshots, 240 markets, 0 errors. ~118/240 markets two-sided.
- Committed `7e8b860`, pushed to GitHub.

### Phase 3 — built & verified (2026-05-21)

**Decisions:** 1-year weekly GEFS backfill; Gaussian distribution.

- `src/model/daily_high.py`, `bias.py`, `fairvalue.py`; `scripts/backfill_gefs.py`,
  `fit_bias.py`, `compare_fairvalue.py`; 23 tests.
- **Verified:** `pytest` → 51 passed. 53 GEFS runs backfilled; 7,076 bias pairs,
  mean RMSE 3.67 °F; plausible fair values vs. live Kalshi prices.
- **Known issues (for Phase 6):** ill-conditioned bias slopes on low-variance
  summer cells; ensemble dispersion vs. the market needs checking.
- Committed `2a9fe01`, pushed to GitHub.

### Phase 2 — built & verified (2026-05-21)

**Decisions:** observation source = NWS CLI via IEM archive; backfill from 2019.

- `src/ingest/observations.py`, `scripts/ingest_observations.py`,
  `fetch_kalshi_rules.py`; 8 tests.
- `config.yaml`: 20 `resolution_*` filled; **Houston corrected KIAH → KHOU**.
- **Verified:** `pytest` → 28 passed; 52,506 observation rows.
- Committed `5e170b0`, pushed to GitHub.

### Phase 1 — built & verified (2026-05-21)

**Decisions:** lead times 3-hourly to +168 h; grid 0.5° `pgrb2a`.

- `src/ingest/gefs.py`, `src/common/storage.py`, `scripts/ingest_gefs.py`;
  14 tests.
- **Verified:** `pytest` → 20 passed; GEFS 2026-05-21 12Z, 34,720 rows;
  cfgrib + ecCodes confirmed.
- Committed `56d3751`, pushed to GitHub.

### Phase 0 — built & verified (2026-05-21)

**Decisions:** 20 cities; `config.yaml` committed to git.

- Repo skeleton; `src/common/config.py`; 6 tests.
- Committed `4a8c653`; GitHub repo created.
