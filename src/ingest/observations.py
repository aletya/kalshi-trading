"""Observations ingestion: historical observed daily high temperatures.

Source: the NWS Climatological Report (Daily) — the "CLI" product — which is the
exact source Kalshi settles its temperature markets on (verified from live
Kalshi contract rules; see README). We pull the CLI archive from the Iowa
Environmental Mesonet (IEM), which parses and stores every CLI report.

By ingesting the CLI ``high`` directly we inherit Kalshi's settlement definition
exactly: the daily high is whatever the CLI reports — midnight-to-midnight local
standard time, whole °F. We deliberately do NOT reconstruct the day window from
hourly observations; that would reintroduce the resolution-mismatch risk.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from src.common.config import Station

IEM_CLI_URL = "https://mesonet.agron.iastate.edu/json/cli.py"
SOURCE = "IEM-CLI"


def fetch_cli_year(client: httpx.Client, station_id: str, year: int) -> list[dict]:
    """Fetch one calendar year of CLI records for a station from IEM.

    IEM's cli.py returns one JSON object with a ``results`` array, one record
    per day. Raises httpx.HTTPStatusError on a bad response.
    """
    resp = client.get(
        IEM_CLI_URL, params={"station": station_id, "fmt": "json", "year": year}
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def coerce_high(value) -> float | None:
    """Coerce a CLI ``high`` field to °F, or None if missing.

    CLI highs are whole °F integers; missing days appear as ``"M"`` or null.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None  # "M" (missing) and other non-numeric markers


def normalize_cli_records(raw: list[dict], station_id: str) -> list[dict]:
    """Turn raw IEM CLI records into tidy observation rows.

    Records with a missing date or a missing/non-numeric high are dropped — we
    never want a fabricated observed high in the ground-truth table.
    """
    rows: list[dict] = []
    for rec in raw:
        valid = rec.get("valid")
        high = coerce_high(rec.get("high"))
        if not valid or high is None:
            continue
        rows.append(
            {
                "station_id": station_id,
                "date": dt.date.fromisoformat(valid),
                "observed_high_f": high,
                "high_time": rec.get("high_time"),
                "cli_product": rec.get("product"),
                "source": SOURCE,
            }
        )
    return rows


def filter_by_date_range(
    rows: list[dict], start: dt.date, end: dt.date
) -> list[dict]:
    """Keep only rows whose ``date`` falls within [start, end] inclusive."""
    return [r for r in rows if start <= r["date"] <= end]


@dataclass
class ObsIngestResult:
    """Outcome of one ingest_observations call."""

    path: Path | None
    rows: int
    per_station: dict[str, tuple[int, str | None, str | None]] = field(
        default_factory=dict
    )  # station_id -> (row_count, first_date, last_date)
    stations_failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rows > 0


def ingest_observations(
    *,
    stations: list[Station],
    start: dt.date,
    end: dt.date,
    out_path: Path,
    timeout: float = 60.0,
) -> ObsIngestResult:
    """Backfill observed daily highs for ``stations`` over [start, end].

    A station whose CLI fetch fails is recorded in ``stations_failed`` and
    skipped; the rest still produce a Parquet table.
    """
    retrieved_at = dt.datetime.now(dt.timezone.utc)
    all_rows: list[dict] = []
    per_station: dict[str, tuple[int, str | None, str | None]] = {}
    failed: list[str] = []

    with httpx.Client(timeout=timeout) as client:
        for station in stations:
            try:
                raw: list[dict] = []
                for year in range(start.year, end.year + 1):
                    raw.extend(fetch_cli_year(client, station.id, year))
            except httpx.HTTPError:
                failed.append(station.id)
                continue

            rows = filter_by_date_range(
                normalize_cli_records(raw, station.id), start, end
            )
            for row in rows:
                row["retrieved_at"] = retrieved_at
            all_rows.extend(rows)

            if rows:
                dates = [r["date"] for r in rows]
                per_station[station.id] = (
                    len(rows),
                    min(dates).isoformat(),
                    max(dates).isoformat(),
                )
            else:
                per_station[station.id] = (0, None, None)

    if not all_rows:
        return ObsIngestResult(
            path=None, rows=0, per_station=per_station, stations_failed=failed
        )

    # Local import keeps storage's polars dependency out of offline unit tests.
    from src.common import storage

    written = storage.write_parquet(all_rows, out_path)
    return ObsIngestResult(
        path=out_path,
        rows=written,
        per_station=per_station,
        stations_failed=failed,
    )
