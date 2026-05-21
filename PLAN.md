# PLAN.md — Living Plan

Updated at the start of every phase. Reflects the **current** phase's plan and
the log of completed phases. See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the
full brief.

---

## Current status

- **Phase:** 4 — Kalshi ingestion & logging
- **State:** ✅ Built and verified; launchd logger running. 🛑 Awaiting
  Checkpoint-4 sign-off before starting Phase 5.

### Decisions on the Phase 4 open questions
1. **Book depth:** store top-of-book (bid/ask/sizes/mid/spread) as columns
   **plus** the full raw order book as a compact JSON blob in the same snapshot
   row — fast spread queries, full depth preserved.
2. **Scheduling:** install a macOS **launchd** job now that runs `log_kalshi.py`
   every 15 min, so history accumulates immediately. README documents how to
   inspect / unload it.

---

## Phase 4 plan — Kalshi ingestion & logging

### Goal
A scheduled logger that snapshots the **full order book** of every Kalshi
temperature market for our 20 cities into SQLite, building our own backtest
dataset. Definition of done: a scheduled command appends order-book snapshots to
`kalshi.db`, and we can query the bid/ask **spread over time** for any market.
The brief says start this running early — Phase 5's backtest needs the history.

### What we already learned (Phase 3)
Live quotes come from the **orderbook endpoint** (`/markets/{ticker}/orderbook`),
*not* the markets-list summary (which never populates `yes_bid`/`yes_ask`). The
book has a `yes` side and a `no` side; best yes ask = `1 − best no bid`. Markets
are liquid (~1–3¢ spreads). The logger is built around the orderbook endpoint.

### Files to create / change

| File | Purpose |
|---|---|
| `src/ingest/kalshi.py` | Fetch markets + orderbooks; parse to snapshots; SQLite schema + writes. |
| `scripts/log_kalshi.py` | One polling pass over all markets — the cron/launchd entry point. |
| `scripts/discover_kalshi_series.py` | One-off helper: find each city's active high-temp series ticker (fills config). |
| `tests/test_kalshi.py` (+ fixtures) | Offline tests: orderbook parsing, ticker parsing, SQLite round-trip. |
| `config.yaml` | Add `kalshi_series` per station (the series to log). |
| `README.md` | Document the schema and how to schedule the logger. |

### `src/ingest/kalshi.py` — key functions
- `parse_orderbook(orderbook_fp) -> Quote` — yes/no ladders → best `yes_bid`,
  `yes_ask` (= 1 − best no bid), sizes, `mid`, `spread`. Pure, unit-tested.
- `parse_target_date(ticker)`, `market_metadata(market)` — pull target date,
  strike type/bounds, title.
- `fetch_markets(client, series_ticker)`, `fetch_orderbook(client, ticker)` —
  REST calls, throttled with retry/backoff on HTTP 429.
- `init_db(path)` — create tables (idempotent).
- `upsert_market(conn, ...)`, `insert_snapshot(conn, ...)`.
- `log_once(config, conn)` — one full polling pass over every configured market.

### SQLite schema (`data/kalshi.db`)
- **`markets`** — one row per market (upserted):
  `ticker` PK, `series_ticker`, `station_id`, `target_date`, `strike_type`,
  `floor_strike`, `cap_strike`, `title`, `first_seen`, `last_seen`.
- **`orderbook_snapshots`** — one row per market per poll:
  `id` PK, `ticker`, `ts` (UTC), `status`, `yes_bid`, `yes_ask`, `yes_bid_qty`,
  `yes_ask_qty`, `mid`, `spread`, and the full raw orderbook (storage choice
  below). `UNIQUE(ticker, ts)`.

`mid` and `spread` are computed at log time and stored, so the
"reconstruct mid/spread for any historical moment" requirement is a plain
`SELECT` — no recomputation needed.

(The `paper_trades` table arrives in Phase 7; this phase creates only the
order-book tables.)

### Scheduling
`log_kalshi.py` does **one** polling pass and exits — cron/launchd calls it
every `kalshi.poll_interval_minutes` (config: 15). No always-on server. ~20
series-list calls + ~240 orderbook calls per pass, throttled (~40 s/pass).

### Tests (offline)
- `parse_orderbook`: crafted yes/no ladders → correct bid/ask/mid/spread; empty
  side → `None`.
- `parse_target_date`: `KXHIGHNY-26MAY22-T70` → 2026-05-22.
- SQLite round-trip on an in-memory db: `init_db`, `upsert_market` (insert then
  update `last_seen`), `insert_snapshot`, and a spread-over-time query.

### Definition of done
`python scripts/log_kalshi.py` appends snapshots to `kalshi.db`; a query returns
the spread time series for a market. Run on a schedule, history accumulates.

### Open questions — RESOLVED
1. ~~Order-book storage depth~~ → top-of-book columns + full raw book JSON blob.
2. ~~Scheduling~~ → install a launchd job now (see Decisions at top).

### Not in Phase 4
No WebSocket feed (REST polling only; WebSocket is a possible later add). No
backtest P&L (Phase 5), no paper trading (Phase 7).

---

## Phase log

### Phase 4 — built & verified (2026-05-21)

**Decisions:** top-of-book columns + full raw order book JSON blob per snapshot;
launchd job installed now.

- `src/ingest/kalshi.py`: orderbook parsing, throttled REST fetch with 429
  backoff, SQLite schema + `log_once` polling pass.
- `scripts/log_kalshi.py` (one pass), `scripts/discover_kalshi_series.py`.
- `config.yaml`: `kalshi_series` added per station (active series discovered);
  dead `kalshi.market_series` removed; `config.py` updated.
- `tests/test_kalshi.py`: 9 offline tests (orderbook/ticker parsing, SQLite
  round-trip).
- launchd agent `com.kalshiweather.logkalshi` installed — runs every 15 min.
- **Verified:** `pytest` → 60 passed. Three logging passes (2 manual + 1 via
  launchd) → 720 snapshots, 240 markets, 0 errors. `kalshi.db` stores `mid`/
  `spread` per snapshot; ~118/240 markets carry a two-sided quote (the rest
  are one-sided — `mid`/`spread` correctly NULL).

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
  bias-corrected highs and real disagreements vs. live Kalshi prices.
- **Kalshi quotes:** live quotes come from the orderbook endpoint, not the
  markets-list summary; spreads are tight (~1-3¢).
- **Known issues (for Phase 6):** ill-conditioned bias slopes on low-variance
  summer cells; ensemble dispersion vs. the market needs checking.
- Committed `2a9fe01`, pushed to GitHub.

### Phase 2 — built & verified (2026-05-21)

**Decisions:** observation source = NWS CLI via IEM archive; backfill from
2019-01-01.

- `src/ingest/observations.py`, `scripts/ingest_observations.py`,
  `scripts/fetch_kalshi_rules.py`; `tests/test_observations.py` (8 tests).
- `config.yaml`: all 20 `resolution_*` filled from live Kalshi
  `settlement_sources`. **Houston corrected KIAH → KHOU.**
- **Verified:** `pytest` → 28 passed; `ingest_observations` → 52,506 rows
  (2019-01-01 → 2026-05-21).
- Committed `5e170b0`, pushed to GitHub.

### Phase 1 — built & verified (2026-05-21)

**Decisions:** lead times 3-hourly to +168 h; grid 0.5° `pgrb2a`.

- `src/ingest/gefs.py` (`.idx` byte-range subsetting, cfgrib decode),
  `src/common/storage.py`, `scripts/ingest_gefs.py`, `tests/test_gefs.py`.
- **Verified:** `pytest` → 20 passed; live run pulled GEFS 2026-05-21 12Z,
  1736/1736 member-files, 34,720 rows. cfgrib + ecCodes confirmed.
- Committed `56d3751`, pushed to GitHub.

### Phase 0 — built & verified (2026-05-21)

**Decisions:** 20 cities; `config.yaml` committed to git.

- Repo skeleton, `pyproject.toml`, `config.yaml`, `src/common/config.py`,
  `scripts/hello.py`, `tests/test_smoke.py`, `README.md`.
- **Verified:** `pytest` → 6 passed. Committed `4a8c653`; GitHub repo created.
