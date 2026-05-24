# PLAN.md — Living Plan

Updated at the start of every phase. Reflects the **current** phase's plan and
the log of completed phases. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the
full brief.

---

## Current status

- **Phase:** 7 — Paper trading loop
- **State:** ✅ Built and verified; launchd agent running every 6 h. 🛑 Awaiting
  Checkpoint-7 sign-off — the **weeks-long accumulation review** is the real
  Checkpoint 7 and revisits later.

### Decision on the Phase 7 open question
- **Cadence:** every 6 hours (a launchd agent), aligned with the GEFS cycles.

---

## Phase 7 plan — Paper trading loop

### Goal
A scheduled loop that, each cycle, recomputes fair values, checks live Kalshi
prices, records the hold-to-resolution trades the strategy *would* place into
SQLite (**no real orders, no Kalshi trading API**), and settles past paper
positions against the observed high. Definition of done: it runs unattended for
weeks, accumulating paper trades and a live (out-of-sample) calibration record.

### Each cycle (`src/papertrade/runner.py` + `scripts/paper_trade.py`)
1. **Refresh inputs** — ingest the latest GEFS run if not already on disk;
   ingest the last ~10 days of observations (cheap; needed to settle).
2. **Decide new trades** — for every currently-tradeable market with no paper
   position yet: read its most recent order-book snapshot from `kalshi.db`
   (the Phase 4 logger keeps it ≤15 min fresh), compute the bias-corrected fair
   value from the latest GEFS run, and apply the same rule as the backtester
   (buy YES at the ask / NO at 1−bid when `edge ≥ min_edge`). Record qualifying
   trades to a new `paper_trades` table. One position per market.
3. **Settle** — for open paper positions whose target date now has an observed
   CLI high: settle YES/NO, compute P&L (executed at the entry quote — spread
   paid) and the mid-to-mid counterfactual, mark `settled`.
4. **Report** — open/settled counts, settled P&L net of spread, hit rate, and
   the Brier score of settled trades (the live calibration record).

This reuses the Phase 5 engine (`bucket_from_strike`, `market_outcome`,
`evaluate_pnl`, the fair-value path) — no logic is duplicated. Entry at the ask
is consistent with "a limit order at fair value": we only trade when fair value
already exceeds the ask, so such a limit crosses and fills at the ask.

### `paper_trades` table (in `kalshi.db`)
`id`, `ticker` (UNIQUE — one position/market), `station_id`, `target_date`,
`strike_type`, `floor_strike`, `cap_strike`, `bucket_label`, `entry_ts`,
`gefs_init`, `our_prob`, `side`, `entry_yes_bid`, `entry_yes_ask`,
`entry_price`, `edge`, `status` (`open`/`settled`), `observed_high`, `outcome`,
`pnl`, `pnl_mid`, `settled_ts`.

### Files to create
| File | Purpose |
|---|---|
| `src/papertrade/runner.py` | `paper_trades` schema, one decide+settle pass. |
| `scripts/paper_trade.py` | Cycle entry point (refresh → decide → settle → report). |
| `scripts/com.kalshiweather.papertrade.plist` | launchd agent. |
| `tests/test_papertrade.py` | Synthetic tests: record a trade, settle it, P&L. |

### Scheduling
A launchd agent runs `paper_trade.py` on a cadence (see open question). It runs
unattended; the Phase 4 order-book logger keeps feeding `kalshi.db` in parallel.

### Tests (synthetic, known-answer)
- `paper_trades` round-trip on an in-memory db: record an open trade, settle it,
  verify status/outcome/pnl.
- A market already holding a position is not re-traded.
- One decide+settle pass on a synthetic db + GEFS + observations.

### Definition of done
`paper_trade.py` runs a full cycle (verified once now); the launchd agent then
accumulates paper trades and settled-trade calibration over the coming weeks.

### Open question — RESOLVED
1. ~~Paper-trader cadence~~ → every 6 hours (launchd). See Decision at top.

### Not in Phase 7 (out of scope, per the brief)
Real-money execution via Kalshi's trading API. Only worth discussing if Phase 7
shows a calibrated, costs-included, positive edge sustained over weeks — a
separate brief.

---

## Phase log

### Phase 7 — built & verified (2026-05-22)

**Decision:** paper-trader cadence = every 6 h (launchd).

- `src/papertrade/runner.py`: `paper_trades` schema, decide + settle pass,
  reuses Phase 5 engine (`decide_trade` factored out of engine for DRY).
- `scripts/paper_trade.py`: refresh GEFS → refresh obs (merge) → decide →
  settle → report.
- Merge-aware `ingest_observations` so re-running for a recent window never
  destroys the 7-year historical table.
- launchd agent `com.kalshiweather.papertrade` — every 6 h.
- `tests/test_papertrade.py`: 5 tests incl. a synthetic decide→settle cycle.
- **Verified:** `pytest` → 85 passed. First live cycle ingested GEFS
  2026-05-22 18Z and **opened 75 paper trades** (with 240 markets too late —
  target day started — and 32 no-edge / 13 one-sided). 0 settled yet.
- Committed and pushed.

### Phase 6 — built & verified (2026-05-22)

**Decision:** raw + in-sample bias-corrected calibration; no time-split.

- `src/backtest/calibration.py`, `scripts/run_calibration.py`,
  `tests/test_calibration.py` (13 tests).
- **Verified:** `pytest` → 80 passed. ~57k predictions: raw ensemble slope
  **0.77** (over-confident); bias-corrected slope **0.88**, Brier 0.19→0.15
  (in-sample). Mild residual under-dispersion — proceeding to Phase 7 as-is
  per checkpoint decision.
- Committed `da89536`, pushed to GitHub.

### Phase 5 — built & verified (2026-05-21)

- `src/backtest/engine.py`, `scripts/run_backtest.py`, `tests/test_engine.py`
  (11 tests). First-qualifying-edge, no lookahead, costs included.
- **Verified:** `pytest` → 70 passed; 0 completed trades yet (logger just
  started — expected). Committed `4f5892f`.

### Phase 4 — built & verified (2026-05-21)

- `src/ingest/kalshi.py` + scripts; 9 tests. launchd order-book logger every
  15 min. 720 snapshots logged, 0 errors. Committed `7e8b860`.

### Phase 3 — built & verified (2026-05-21)

- `src/model/daily_high.py`, `bias.py`, `fairvalue.py` + scripts; 23 tests.
  53 GEFS runs backfilled; bias mean RMSE 3.67 °F. Committed `2a9fe01`.

### Phase 2 — built & verified (2026-05-21)

- `src/ingest/observations.py` + scripts; 8 tests. Resolution verified;
  **Houston corrected KIAH → KHOU**. 52,506 observation rows. Committed `5e170b0`.

### Phase 1 — built & verified (2026-05-21)

- `src/ingest/gefs.py` + script; 14 tests. cfgrib + ecCodes confirmed.
  Committed `56d3751`.

### Phase 0 — built & verified (2026-05-21)

- Repo skeleton; `src/common/config.py`; 6 tests. Committed `4a8c653`;
  public GitHub repo created.
