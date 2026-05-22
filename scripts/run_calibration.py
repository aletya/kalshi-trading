"""CLI: calibration report for the fair-value model.

Builds (predicted probability, outcome) pairs from the backfilled GEFS runs and
observed CLI highs, then reports a reliability table, an ASCII reliability
diagram, and Brier scores — for the raw ensemble and the bias-corrected model.

    python scripts/run_calibration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest import calibration  # noqa: E402
from src.common import storage  # noqa: E402
from src.common.config import load_config  # noqa: E402
from src.model import bias  # noqa: E402


def _diagram_row(b: calibration.ReliabilityBin, width: int = 40) -> str:
    """One reliability-diagram row: 'P' = mean predicted, 'O' = observed freq."""
    if not b.count:
        return f"  {b.lo:4.0%}-{b.hi:<4.0%}  (empty)"
    cells = [" "] * (width + 1)
    p_pos = min(width, round(b.mean_predicted * width))
    o_pos = min(width, round(b.observed_frequency * width))
    cells[p_pos] = "P"
    cells[o_pos] = "O" if o_pos != p_pos else "X"
    bar = "".join(cells)
    return (
        f"  {b.lo:4.0%}-{b.hi:<4.0%} |{bar}| "
        f"pred {b.mean_predicted:4.0%}  obs {b.observed_frequency:4.0%}  "
        f"n={b.count}"
    )


def _report(label: str, predictions: list) -> None:
    print(f"\n{'=' * 70}\n{label}  ({len(predictions)} predictions)\n{'=' * 70}")
    if not predictions:
        print("  (no predictions — need backfilled GEFS runs + observations)")
        return

    bins = calibration.reliability_table(predictions)
    print("  Reliability diagram  (P = predicted, O = observed, X = aligned)")
    print(f"  {'':9} 0%{' ' * 34}100%")
    for b in bins:
        print(_diagram_row(b))

    brier = calibration.brier_score(predictions)
    slope = calibration.calibration_slope(bins)
    print(f"\n  Brier score: {brier:.4f}   (0 = perfect; lower is better)")
    if slope is not None:
        print(f"  Calibration slope: {slope:.2f}")
        if slope < 0.85:
            verdict = "OVER-confident — predictions too extreme (under-dispersed)"
        elif slope > 1.15:
            verdict = "UNDER-confident — predictions too timid (over-dispersed)"
        else:
            verdict = "reasonably calibrated"
        print(f"  Verdict: {verdict}")

    print("\n  Brier by lead time:")
    for lead, (count, lead_brier) in sorted(calibration.brier_by_lead(predictions).items()):
        print(f"    +{lead}d   n={count:6}   Brier {lead_brier:.4f}")


def main() -> int:
    config = load_config()
    observations = calibration.load_observations(
        storage.observations_path(config.paths.observations_dir)
    )
    stations = list(config.stations)
    ensemble_dir = config.paths.ensemble_dir

    raw = calibration.build_predictions(ensemble_dir, observations, stations, None)
    _report("RAW ENSEMBLE  (no bias correction — independent of any fitting)", raw)

    bias_path = storage.bias_model_path(config.paths.data_dir)
    if bias_path.exists():
        model = bias.load_bias_model(bias_path)
        corrected = calibration.build_predictions(
            ensemble_dir, observations, stations, model
        )
        _report(
            "BIAS-CORRECTED  (IN-SAMPLE — bias model was fit on these runs)",
            corrected,
        )
    else:
        print("\n[note] No bias model on disk — run fit_bias.py for the "
              "bias-corrected calibration.")

    print(f"\n{'-' * 70}")
    print("The raw-ensemble numbers are the clean read on ensemble dispersion. "
          "The bias-corrected numbers are in-sample (optimistic); the true "
          "out-of-sample calibration comes from Phase 7 paper trading.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
