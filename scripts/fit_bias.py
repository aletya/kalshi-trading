"""CLI: build the bias-correction training set and fit the bias model.

Joins every backfilled GEFS run in data/ensemble/ with the observed daily highs,
fits a per-station/per-season linear correction, and writes data/model/bias.json.

    python scripts/fit_bias.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common import storage  # noqa: E402
from src.common.config import load_config  # noqa: E402
from src.model import bias  # noqa: E402


def main() -> int:
    config = load_config()
    obs_path = storage.observations_path(config.paths.observations_dir)
    if not obs_path.exists():
        print(f"[FAIL] No observations at {obs_path}. Run ingest_observations first.")
        return 1

    print("Building training pairs from backfilled GEFS runs ...")
    started = time.monotonic()
    pairs = bias.build_training_pairs(
        config.paths.ensemble_dir, obs_path, list(config.stations)
    )
    if not pairs:
        print("[FAIL] No training pairs. Run backfill_gefs.py first.")
        return 1

    model = bias.fit_bias(pairs)
    out_path = storage.bias_model_path(config.paths.data_dir)
    bias.save_bias_model(model, out_path)
    elapsed = time.monotonic() - started

    print(f"Fitted {len(pairs)} pairs in {elapsed:.0f}s -> {out_path}\n")
    print(f"{'STATION':8} {'SEASON':7} {'N':>5}  {'a':>8} {'b':>7} {'RMSE':>7}  SOURCE")
    print("-" * 60)
    for station in config.stations:
        for season in bias.SEASONS:
            fit = model["fits"][f"{station.id}|{season}"]
            rmse = f"{fit['rmse']:.2f}" if fit["rmse"] == fit["rmse"] else "  n/a"
            print(
                f"{station.id:8} {season:7} {fit['n_pairs']:5d}  "
                f"{fit['a']:8.2f} {fit['b']:7.3f} {rmse:>7}  {fit['source']}"
            )

    # Headline: how good is the correction, season fits only.
    season_fits = [f for f in model["fits"].values() if f["source"] == "season"]
    if season_fits:
        mean_rmse = sum(f["rmse"] for f in season_fits) / len(season_fits)
        print("-" * 60)
        print(
            f"[OK] {len(season_fits)} own-season fits, "
            f"mean RMSE {mean_rmse:.2f} degF; "
            f"{len(model['fits']) - len(season_fits)} cells used a fallback."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
