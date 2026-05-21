"""Offline unit tests for src/ingest/observations.py.

No network: the live IEM CLI fetch is exercised by the CLI run at Checkpoint 2.
"""

import datetime as dt
import json
from pathlib import Path

from src.ingest import observations

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _sample_records() -> list[dict]:
    return json.loads((FIXTURE_DIR / "sample_cli.json").read_text())["results"]


# --- coerce_high ----------------------------------------------------------
def test_coerce_high_numeric():
    assert observations.coerce_high(88) == 88.0
    assert observations.coerce_high("91") == 91.0


def test_coerce_high_missing_markers_become_none():
    assert observations.coerce_high("M") is None
    assert observations.coerce_high(None) is None
    assert observations.coerce_high("") is None


# --- normalize_cli_records ------------------------------------------------
def test_normalize_keeps_only_valid_rows():
    rows = observations.normalize_cli_records(_sample_records(), "KNYC")
    # Fixture has 5 records: 2 good, 1 high="M", 1 high=null, 1 valid=null.
    assert len(rows) == 2
    assert {r["date"] for r in rows} == {
        dt.date(2025, 7, 1),
        dt.date(2025, 7, 2),
    }


def test_normalize_row_shape_and_values():
    rows = observations.normalize_cli_records(_sample_records(), "KNYC")
    first = next(r for r in rows if r["date"] == dt.date(2025, 7, 1))
    assert first["station_id"] == "KNYC"
    assert first["observed_high_f"] == 88.0
    assert first["high_time"] == "0245 PM"
    assert first["source"] == "IEM-CLI"
    assert first["cli_product"] == "202507020600-KOKX-CDUS41-CLINYC"


def test_normalize_parses_dates_as_date_objects():
    rows = observations.normalize_cli_records(_sample_records(), "KNYC")
    assert all(isinstance(r["date"], dt.date) for r in rows)


def test_normalize_empty_input():
    assert observations.normalize_cli_records([], "KNYC") == []


# --- filter_by_date_range -------------------------------------------------
def test_filter_by_date_range_inclusive():
    rows = observations.normalize_cli_records(_sample_records(), "KNYC")
    kept = observations.filter_by_date_range(
        rows, dt.date(2025, 7, 2), dt.date(2025, 7, 31)
    )
    assert len(kept) == 1
    assert kept[0]["date"] == dt.date(2025, 7, 2)


def test_filter_by_date_range_excludes_outside():
    rows = observations.normalize_cli_records(_sample_records(), "KNYC")
    kept = observations.filter_by_date_range(
        rows, dt.date(2030, 1, 1), dt.date(2030, 12, 31)
    )
    assert kept == []
