"""GEFS ensemble ingestion from the public NOAA Open Data S3 bucket.

Given a date, a model-run cycle, and a station list, this downloads 2-metre
temperature for all 31 ensemble members and extracts the value at the nearest
grid point to each station.

Efficiency: every GEFS GRIB file has a sibling ``.idx`` text index listing each
message's byte offset. We parse it, find the ``TMP : 2 m above ground`` message,
and issue an HTTP ``Range`` request for just that ~0.5 MB slice instead of the
~50 MB whole file. No raw GRIB is kept on disk — only extracted Parquet rows.

S3 access is anonymous over plain HTTPS; no AWS account or credentials needed.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import xarray as xr

from src.common.config import Station

S3_BASE_URL = "https://noaa-gefs-pds.s3.amazonaws.com"
GEFS_CYCLES = ("00", "06", "12", "18")


class MessageNotFound(Exception):
    """The requested GRIB message is absent from a .idx file."""


# --------------------------------------------------------------------------
# .idx parsing
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class IdxEntry:
    """One message record from a GRIB .idx file."""

    msg_num: int
    start_byte: int
    variable: str
    level: str


def parse_idx(text: str) -> list[IdxEntry]:
    """Parse a GRIB .idx file into ordered message records.

    Each line looks like:
        4:140987:d=2026052100:TMP:2 m above ground:3 hour fcst:ENS=low-res ctl
    i.e. ``msg_num:start_byte:date:variable:level:forecast:...``
    """
    entries: list[IdxEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(":")
        if len(fields) < 5:
            continue
        try:
            entries.append(
                IdxEntry(
                    msg_num=int(fields[0]),
                    start_byte=int(fields[1]),
                    variable=fields[3],
                    level=fields[4],
                )
            )
        except ValueError:
            # Malformed line — skip it rather than abort the whole file.
            continue
    return entries


def find_temp_byterange(entries: list[IdxEntry]) -> tuple[int, int | None]:
    """Return ``(start_byte, end_byte)`` for the 2 m temperature message.

    ``end_byte`` is ``None`` when it is the last message in the file (the HTTP
    range then runs to end-of-file).

    Raises:
        MessageNotFound: if no ``TMP : 2 m above ground`` message is present.
    """
    for i, entry in enumerate(entries):
        if entry.variable == "TMP" and entry.level == "2 m above ground":
            start = entry.start_byte
            end = entries[i + 1].start_byte - 1 if i + 1 < len(entries) else None
            return start, end
    raise MessageNotFound("No 'TMP : 2 m above ground' message in .idx")


# --------------------------------------------------------------------------
# Keys, members, unit conversions (pure helpers — unit-tested offline)
# --------------------------------------------------------------------------
def member_names(members: int) -> list[str]:
    """GEFS member identifiers: control ``gec00`` + perturbed ``gep01``…``gepNN``."""
    if members < 1:
        raise ValueError("members must be >= 1")
    return ["gec00"] + [f"gep{i:02d}" for i in range(1, members)]


def grib_key(date: dt.date, cycle: str, member: str, fhour: int) -> str:
    """S3 object key for one member's 0.5° pgrb2a GRIB file at a lead time."""
    return (
        f"gefs.{date:%Y%m%d}/{cycle}/atmos/pgrb2ap5/"
        f"{member}.t{cycle}z.pgrb2a.0p50.f{fhour:03d}"
    )


def to_grid_longitude(lon: float) -> float:
    """Convert a signed longitude (-180..180) to the GEFS 0..360 convention."""
    return lon % 360.0


def kelvin_to_fahrenheit(kelvin: float) -> float:
    """Kelvin -> Fahrenheit (Kalshi temperature markets are in °F)."""
    return (kelvin - 273.15) * 9.0 / 5.0 + 32.0


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def fetch_idx(client: httpx.Client, key: str) -> list[IdxEntry]:
    """Download and parse a GRIB .idx file.

    Raises httpx.HTTPStatusError if the index is missing (e.g. run not posted).
    """
    resp = client.get(f"{S3_BASE_URL}/{key}.idx")
    resp.raise_for_status()
    return parse_idx(resp.text)


def download_message(
    client: httpx.Client, key: str, byterange: tuple[int, int | None]
) -> bytes:
    """Range-GET a single GRIB message identified by ``byterange``."""
    start, end = byterange
    range_header = f"bytes={start}-{'' if end is None else end}"
    resp = client.get(f"{S3_BASE_URL}/{key}", headers={"Range": range_header})
    resp.raise_for_status()
    return resp.content


def _candidate_runs(now: dt.datetime, count: int) -> list[tuple[dt.date, str]]:
    """Most-recent-first list of (date, cycle) GEFS runs at/just before ``now``."""
    cycle_hour = (now.hour // 6) * 6
    anchor = now.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
    runs = []
    for i in range(count):
        t = anchor - dt.timedelta(hours=6 * i)
        runs.append((t.date(), f"{t.hour:02d}"))
    return runs


def find_latest_run(client: httpx.Client, max_lookback: int = 8) -> tuple[dt.date, str]:
    """Probe S3 for the most recent GEFS run that has data posted.

    Checks for the control member's f003 .idx — its presence means the run is up.
    """
    now = dt.datetime.now(dt.timezone.utc)
    for date, cycle in _candidate_runs(now, max_lookback):
        key = grib_key(date, cycle, "gec00", 3)
        resp = client.head(f"{S3_BASE_URL}/{key}.idx")
        if resp.status_code == 200:
            return date, cycle
    raise RuntimeError(
        f"No GEFS run found in the last {max_lookback} cycles — S3 may be lagging."
    )


# --------------------------------------------------------------------------
# GRIB decoding (this is the step that exercises cfgrib + ecCodes)
# --------------------------------------------------------------------------
def extract_station_temps(
    grib_bytes: bytes, stations: list[Station]
) -> dict[str, tuple[float, float, float]]:
    """Decode a single 2 m temperature GRIB message and sample each station.

    Returns ``station_id -> (temp_kelvin, grid_latitude, grid_longitude)`` where
    the grid coordinates are the actual nearest GEFS point used.
    """
    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        tmp.write(grib_bytes)
        tmp.flush()
        # indexpath="" stops cfgrib writing a .idx sidecar next to the temp file.
        dataset = xr.open_dataset(
            tmp.name, engine="cfgrib", backend_kwargs={"indexpath": ""}
        )
        try:
            temp_field = dataset["t2m"]
            results: dict[str, tuple[float, float, float]] = {}
            for station in stations:
                point = temp_field.sel(
                    latitude=station.latitude,
                    longitude=to_grid_longitude(station.longitude),
                    method="nearest",
                )
                results[station.id] = (
                    float(point.values),
                    float(point.latitude.values),
                    float(point.longitude.values),
                )
            return results
        finally:
            dataset.close()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
@dataclass
class IngestResult:
    """Outcome of one ingest_run call."""

    path: Path | None
    rows: int
    expected_files: int
    retrieved_files: int
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rows > 0


def ingest_run(
    *,
    date: dt.date,
    cycle: str,
    stations: list[Station],
    forecast_hours: list[int],
    members: int,
    out_path: Path,
    workers: int = 8,
    timeout: float = 60.0,
    progress: Callable[[int, int], None] | None = None,
) -> IngestResult:
    """Ingest one GEFS run: download every member × lead time, write Parquet.

    A missing or malformed member-file is logged into ``IngestResult.missing``
    and skipped; partial runs still produce a Parquet slice for what was found.
    """
    members_list = member_names(members)
    tasks = [(m, fh) for m in members_list for fh in forecast_hours]

    def run_task(client: httpx.Client, member: str, fhour: int) -> list[dict]:
        key = grib_key(date, cycle, member, fhour)
        entries = fetch_idx(client, key)
        byterange = find_temp_byterange(entries)
        grib_bytes = download_message(client, key, byterange)
        temps = extract_station_temps(grib_bytes, stations)

        init_time = dt.datetime.combine(
            date, dt.time(int(cycle)), tzinfo=dt.timezone.utc
        )
        valid_time = init_time + dt.timedelta(hours=fhour)
        rows: list[dict] = []
        for station in stations:
            temp_k, grid_lat, grid_lon = temps[station.id]
            rows.append(
                {
                    "station_id": station.id,
                    "member": member,
                    "init_time": init_time,
                    "cycle": cycle,
                    "forecast_hour": fhour,
                    "valid_time": valid_time,
                    "temp_2m_k": temp_k,
                    "temp_2m_f": kelvin_to_fahrenheit(temp_k),
                    "grid_latitude": grid_lat,
                    "grid_longitude": grid_lon,
                }
            )
        return rows

    all_rows: list[dict] = []
    missing: list[str] = []
    done = 0

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(run_task, client, member, fhour): (member, fhour)
                for member, fhour in tasks
            }
            for future in as_completed(futures):
                member, fhour = futures[future]
                try:
                    all_rows.extend(future.result())
                except (httpx.HTTPError, MessageNotFound) as exc:
                    missing.append(f"{member}/f{fhour:03d} ({type(exc).__name__})")
                done += 1
                if progress is not None:
                    progress(done, len(tasks))

    retrieved = len(tasks) - len(missing)
    if not all_rows:
        return IngestResult(
            path=None,
            rows=0,
            expected_files=len(tasks),
            retrieved_files=0,
            missing=missing,
        )

    # Local import keeps storage's polars dependency out of offline unit tests
    # that only touch the pure helpers above.
    from src.common import storage

    written = storage.write_parquet(all_rows, out_path)
    return IngestResult(
        path=out_path,
        rows=written,
        expected_files=len(tasks),
        retrieved_files=retrieved,
        missing=missing,
    )
