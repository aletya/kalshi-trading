"""Phase 0 smoke script.

Confirms the environment is wired up: loads config.yaml, prints the configured
stations, and reports environment basics. No GEFS, Kalshi, or model code here.

Run from the repo root:

    python scripts/hello.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `import src...` when run as a plain script from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.config import load_config  # noqa: E402


def main() -> int:
    print("=" * 70)
    print("Kalshi Weather Research System — Phase 0 smoke check")
    print("=" * 70)
    print(f"Python:    {sys.version.split()[0]}")
    print(f"Repo root: {REPO_ROOT}")

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 — surface any config problem plainly
        print(f"\n[FAIL] Could not load config.yaml: {exc}")
        return 1

    print(f"\nLoaded config.yaml — {len(config.stations)} stations:\n")
    print(f"  {'ID':6} {'CITY':18} {'LAT':>9} {'LON':>10}  RESOLUTION")
    print(f"  {'-' * 6} {'-' * 18} {'-' * 9} {'-' * 10}  {'-' * 24}")
    for s in config.stations:
        status = "verified" if s.resolution_verified else "unverified (Phase 2)"
        print(
            f"  {s.id:6} {s.name:18} {s.latitude:9.4f} {s.longitude:10.4f}  {status}"
        )

    print(
        f"\nGEFS:      bucket={config.gefs.s3_bucket}, members={config.gefs.members}, "
        f"runs={list(config.gefs.model_runs)}"
    )
    print(
        f"Strategy:  min_edge={config.strategy.min_edge}, "
        f"edge>spread={config.strategy.require_edge_exceeds_spread}"
    )
    print(f"Database:  {config.paths.database}")
    print("\n[OK] Environment and config look good. Phase 0 skeleton is live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
