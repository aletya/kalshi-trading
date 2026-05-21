# Kalshi Weather Trading Research System — Project Brief

> This is the kickoff brief for Claude Code. It defines what to build, in what
> order, and where to stop and ask the human for feedback. The checkpoints are
> not optional.

## 0. Operating instructions for Claude Code

- **Plan before coding.** At the start of each phase, write a short plan (files,
  key functions, data shapes) into `PLAN.md` and stop for human approval before
  writing code.
- **Work phase by phase.** Do not jump ahead. Each phase has a definition of
  done and a checkpoint. Do not begin phase N+1 until the human approves phase N.
- **Ask for feedback often.** At every 🛑 CHECKPOINT, stop, summarize what was
  built, show how to run/verify it, and explicitly ask the human whether it
  looks right.
- **Keep it honest.** This is a research system. Its job is to find out whether
  an edge exists, not to assume one does. "The market is efficient after costs"
  is a successful, valid result. Never hide the bid-ask spread in any P&L number.
- **No real money, no live trading, in any phase.** Execution is out of scope.
  Everything here is data, modeling, backtesting, and paper trading only.
- **Environment:** MacBook Pro 2024 (Apple Silicon, arm64). Python 3.11+.
  Everything runs locally. AWS is used only as a free NOAA Open Data source — no
  AWS account or credentials required.

## 1. What we are building (and why)

A research system that tests whether Kalshi's daily temperature markets are
mispriced relative to a probability forecast derived from NOAA's GEFS ensemble.

**Thesis:** Kalshi temperature markets are bucketed ("high temp 70–71°F"). Most
traders anchor on a single headline forecast number and discard the uncertainty.
NOAA's GEFS is a 31-member ensemble — a full probability distribution. If we
convert the ensemble into bucket probabilities carefully (with station-level
bias correction), we may find buckets where our probability disagrees with the
market price by more than the spread. That gap, if it persists and if our model
is calibrated, is a candidate edge.

**Strategy:** hold-to-resolution. Buy a mispriced bucket, hold to settlement,
let the actual observed temperature decide. Crosses the spread once, removes
exit-liquidity risk. The cost: 100% of the weight is on our fair-value model
being well-calibrated. Calibration measurement is a first-class part of the
system.

**Known risks the system must respect:**
- Weather markets are thin; the spread is a heavy tax on every trade.
- Resolution rules are the #1 source of fake "edges." Exact NWS station,
  observation window, rounding, day-cutoff — read from Kalshi contract rules,
  never assumed.
- An apparent edge is often just model error. Hence calibration.

## 2. Tech stack

- **Language:** Python 3.11+, virtual env via `uv` (preferred on Apple Silicon).
- **GRIB / ensemble:** `xarray` + `cfgrib` (uses ecCodes). Install ecCodes via
  Homebrew (`brew install eccodes`); verify cfgrib finds it in Phase 1.
- **Data wrangling:** `polars` preferred (esp. for logs); `pandas` acceptable.
- **Storage:** SQLite for Kalshi order-book logs and trade records; Parquet
  (partitioned by date) for ensemble slices and observations.
- **HTTP:** `httpx`.
- **Config:** a single `config.yaml` — no scattered hardcoded values.
- **Testing:** `pytest`.
- **Scheduling (later phases):** cron or launchd on macOS — no always-on server.

## 3. Repository layout (target end state)

```
kalshi-weather/
├── PROJECT_BRIEF.md        # this file
├── PLAN.md                 # living plan, updated each phase
├── README.md               # how to set up & run
├── config.yaml             # stations, markets, thresholds
├── pyproject.toml
├── data/                   # gitignored — local data store
│   ├── ensemble/           # extracted GEFS slices (Parquet)
│   ├── observations/       # historical observed highs (Parquet)
│   └── kalshi.db           # SQLite: order-book logs + paper trades
├── src/
│   ├── ingest/
│   │   ├── gefs.py         # pull + extract GEFS from NOAA S3
│   │   ├── observations.py # pull observed highs from api.weather.gov
│   │   └── kalshi.py       # pull Kalshi markets + order book
│   ├── model/
│   │   ├── bias.py         # station-level bias correction (MOS-lite)
│   │   └── fairvalue.py    # ensemble -> per-bucket probabilities
│   ├── backtest/
│   │   ├── engine.py       # replay model vs. historical Kalshi prices
│   │   └── calibration.py  # predicted vs. realized frequency
│   ├── papertrade/
│   │   └── runner.py       # live paper trading loop (no real orders)
│   └── common/
│       ├── config.py
│       └── storage.py
├── scripts/                # thin CLI entry points
├── notebooks/              # exploratory analysis
└── tests/
```

This is the destination. It emerges phase by phase — do not create it all at once.

## 4. Build phases

Each phase: plan → approval → build → verify → 🛑 checkpoint → approval.

### Phase 0 — Project skeleton & environment
Initialize repo, `pyproject.toml`, virtual env, `.gitignore` (must ignore
`data/`), pytest wired up, empty `config.yaml` with documented fields.
`README.md` with Apple Silicon setup steps including the ecCodes Homebrew step.
**Done when:** pytest runs (even with zero tests), env activates, a hello-style
script runs.
**🛑 CHECKPOINT 0:** Show the layout and `config.yaml` fields. Ask whether the
config fields cover what the human wants to configure.

### Phase 1 — GEFS ingestion (riskiest phase, do it first)
`src/ingest/gefs.py`: given a date, model run (00/06/12/18Z), and a station
list, download GEFS GRIB from the `noaa-gefs-pds` S3 bucket, extract 2-meter
temperature for all ensemble members at the nearest grid point to each station,
write tidy Parquet slices. Prove cfgrib + ecCodes works. Handle missing runs,
partial data, nearest-grid-point lookup.
**Done when:** one command pulls a real recent GEFS run and produces a Parquet
file with `(station, member, valid_time, temp_2m)` rows.
**🛑 CHECKPOINT 1:** Show a sample of extracted data. Confirm stations and
variable look right.

### Phase 2 — Observations ingestion (ground truth)
`src/ingest/observations.py`: pull historical observed daily high temps for each
target station from `api.weather.gov` (and/or NOAA historical datasets). Encode
the resolution-source detail carefully — match the station and daily-high
definition to how Kalshi actually settles. Document the assumption in code
comments and `README.md`.
**Done when:** a command produces a Parquet table of `(station, date, observed_high)`.
**🛑 CHECKPOINT 2:** Discuss whether the observation source matches Kalshi's
resolution rules. This is the #1 fake-edge trap — do not hand-wave it.

### Phase 3 — Fair-value engine
`src/model/bias.py`: simple per-station, per-season bias-correction regression
fit on (raw GEFS forecast, observed high) pairs.
`src/model/fairvalue.py`: pure function — `(ensemble slice, station, date,
bucket definitions) -> probability per bucket`. Build the empirical distribution
from members, apply bias correction, integrate over each bucket. Unit-tested
with synthetic ensembles where the answer is known.
**Done when:** given a real ensemble slice and a set of buckets, outputs a
probability vector summing to ~1.
**🛑 CHECKPOINT 3:** Show fair-value output for a few real markets next to
actual Kalshi prices. Discuss whether disagreements look plausible.

### Phase 4 — Kalshi ingestion & logging
`src/ingest/kalshi.py`: pull temperature markets and full order book (bid/ask,
not just last price) via Kalshi's REST API; log timestamped snapshots to SQLite.
WebSocket feed optional later. A logging script meant to run on a schedule to
accumulate history — our own backtest dataset; start it early.
**Done when:** a scheduled command appends order-book snapshots to `kalshi.db`;
we can query the spread over time.
**🛑 CHECKPOINT 4:** Confirm logged fields suffice (must reconstruct mid price
and spread for any historical moment).

### Phase 5 — Backtester
`src/backtest/engine.py`: replay historical GEFS runs against logged Kalshi
prices. For each market/day: model probability, market mid, market quote, the
trade the strategy would take, the realized outcome. Every P&L figure subtracts
the spread actually paid — no "mid-to-mid" fantasy P&L. Output a clear per-trade
and aggregate report.
**Done when:** runs over accumulated logs and produces a P&L + hit-rate report
with costs included.
**🛑 CHECKPOINT 5:** Review results. Be explicit if the answer is "no edge after
costs."

### Phase 6 — Calibration harness
`src/backtest/calibration.py`: bucket every prediction by predicted probability
(0–10%, 10–20%, …) and compare to realized frequency. Produce a calibration
plot/table and a metric (Brier score, reliability).
**Done when:** a calibration report over all backtest/paper predictions.
**🛑 CHECKPOINT 6:** Discuss calibration. If poorly calibrated, the model is the
problem — iterate on Phases 3/6 before trusting any edge.

### Phase 7 — Paper trading loop
`src/papertrade/runner.py`: a scheduled loop that, after each GEFS run,
recomputes fair values, checks live Kalshi prices, and records the trades it
would place (hold-to-resolution, limit orders at/inside fair value) into
SQLite — no real orders, no Kalshi trading API calls. Settles paper positions
against observed outcomes; feeds the calibration harness with live data.
**Done when:** runs unattended for weeks, accumulating paper trades and a live
calibration record.
**🛑 CHECKPOINT 7:** Review several weeks of live paper results and calibration.

### Out of scope
Real-money execution via Kalshi's trading API is deliberately excluded. Worth
discussing only after Phase 7 shows a calibrated, costs-included, positive edge
sustained over weeks of live paper trading. That is a separate brief.

## 5. Cross-cutting requirements

- **Costs are never hidden.** The bid-ask spread is subtracted from every P&L
  number, everywhere.
- **Resolution rules are sacred.** Station identity, observation window,
  rounding, day-cutoff are read from Kalshi contract rules and encoded
  explicitly. Any assumption is documented in `README.md`.
- **`PLAN.md` is a living document.** Updated at the start of every phase.
- **Tests accompany logic.** Fair-value engine and bias correction get unit
  tests with known-answer synthetic inputs.
- **No raw GRIB hoarding.** Extract needed slices, write Parquet, discard raw
  GRIB. Storage stays in the low single-digit GB.
- **Reproducibility.** Any result can be regenerated from a single documented
  command.
