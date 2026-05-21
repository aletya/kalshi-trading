"""Load and validate config.yaml into typed objects.

This is the single entry point for configuration. Nothing in the codebase
should read config.yaml directly or hardcode stations/thresholds — call
``load_config()`` and pass the resulting ``Config`` around.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Repo root = two levels up from this file (src/common/config.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ConfigError(ValueError):
    """Raised when config.yaml is missing required fields or has bad values."""


@dataclass(frozen=True)
class Station:
    """A single weather station / city we forecast and trade.

    ``id``, ``latitude``, ``longitude`` are placeholders good enough for GEFS
    nearest-grid-point lookup. ``resolution_station`` / ``resolution_notes``
    stay empty until verified against Kalshi contract rules in Phase 2.
    """

    id: str
    name: str
    kalshi_city: str
    latitude: float
    longitude: float
    timezone: str
    kalshi_series: str = ""
    resolution_station: str = ""
    resolution_notes: str = ""

    @property
    def resolution_verified(self) -> bool:
        """True once Phase 2 has confirmed the Kalshi settlement station."""
        return bool(self.resolution_station.strip())


@dataclass(frozen=True)
class GefsConfig:
    s3_bucket: str
    model_runs: tuple[str, ...]
    members: int
    forecast_hours: tuple[int, ...]
    variable: str


@dataclass(frozen=True)
class KalshiConfig:
    api_base: str
    poll_interval_minutes: int


@dataclass(frozen=True)
class StrategyConfig:
    min_edge: float
    max_position_per_market: int
    require_edge_exceeds_spread: bool


@dataclass(frozen=True)
class Paths:
    """Storage paths, resolved to absolute paths under the repo root."""

    data_dir: Path
    ensemble_dir: Path
    observations_dir: Path
    database: Path


@dataclass(frozen=True)
class Config:
    stations: tuple[Station, ...]
    gefs: GefsConfig
    kalshi: KalshiConfig
    strategy: StrategyConfig
    paths: Paths

    def station(self, station_id: str) -> Station:
        """Look up a station by its ``id`` (e.g. ``"KNYC"``)."""
        for s in self.stations:
            if s.id == station_id:
                return s
        raise KeyError(f"No station with id {station_id!r} in config.")


def _require(mapping: dict, key: str, context: str):
    if key not in mapping:
        raise ConfigError(f"Missing required key {key!r} in {context}.")
    return mapping[key]


def _resolve(path_str: str) -> Path:
    """Resolve a config path string to an absolute path under the repo root."""
    p = Path(path_str)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _parse_forecast_hours(value) -> tuple[int, ...]:
    """Accept either an explicit list of hours or a {start, stop, step} mapping.

    The mapping form is expanded inclusively, e.g. {3, 168, 3} -> (3, 6, ..., 168).
    """
    if isinstance(value, dict):
        for key in ("start", "stop", "step"):
            if key not in value:
                raise ConfigError(f"gefs.forecast_hours mapping missing key {key!r}.")
        start, stop, step = int(value["start"]), int(value["stop"]), int(value["step"])
        if step <= 0:
            raise ConfigError("gefs.forecast_hours.step must be positive.")
        if stop < start:
            raise ConfigError("gefs.forecast_hours.stop must be >= start.")
        return tuple(range(start, stop + 1, step))
    if isinstance(value, (list, tuple)):
        if not value:
            raise ConfigError("gefs.forecast_hours list must not be empty.")
        return tuple(int(h) for h in value)
    raise ConfigError(
        "gefs.forecast_hours must be a list or a {start, stop, step} mapping."
    )


def _parse_station(raw: dict, index: int) -> Station:
    ctx = f"stations[{index}]"
    lat = float(_require(raw, "latitude", ctx))
    lon = float(_require(raw, "longitude", ctx))
    if not -90.0 <= lat <= 90.0:
        raise ConfigError(f"{ctx}: latitude {lat} out of range [-90, 90].")
    if not -180.0 <= lon <= 180.0:
        raise ConfigError(f"{ctx}: longitude {lon} out of range [-180, 180].")
    return Station(
        id=str(_require(raw, "id", ctx)),
        name=str(_require(raw, "name", ctx)),
        kalshi_city=str(_require(raw, "kalshi_city", ctx)),
        latitude=lat,
        longitude=lon,
        timezone=str(_require(raw, "timezone", ctx)),
        kalshi_series=str(raw.get("kalshi_series", "") or ""),
        resolution_station=str(raw.get("resolution_station", "") or ""),
        resolution_notes=str(raw.get("resolution_notes", "") or ""),
    )


def load_config(path: str | Path | None = None) -> Config:
    """Load, parse, and validate config.yaml.

    Args:
        path: optional override; defaults to ``config.yaml`` at the repo root.

    Raises:
        ConfigError: if the file is missing, empty, or malformed.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {config_path} did not parse to a mapping.")

    raw_stations = _require(raw, "stations", "config")
    if not raw_stations:
        raise ConfigError("config: 'stations' must list at least one station.")
    stations = tuple(_parse_station(s, i) for i, s in enumerate(raw_stations))

    ids = [s.id for s in stations]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"config: duplicate station ids in {ids}.")

    g = _require(raw, "gefs", "config")
    gefs = GefsConfig(
        s3_bucket=str(_require(g, "s3_bucket", "gefs")),
        model_runs=tuple(str(r) for r in _require(g, "model_runs", "gefs")),
        members=int(_require(g, "members", "gefs")),
        forecast_hours=_parse_forecast_hours(_require(g, "forecast_hours", "gefs")),
        variable=str(_require(g, "variable", "gefs")),
    )

    k = _require(raw, "kalshi", "config")
    kalshi = KalshiConfig(
        api_base=str(_require(k, "api_base", "kalshi")),
        poll_interval_minutes=int(_require(k, "poll_interval_minutes", "kalshi")),
    )

    s = _require(raw, "strategy", "config")
    strategy = StrategyConfig(
        min_edge=float(_require(s, "min_edge", "strategy")),
        max_position_per_market=int(_require(s, "max_position_per_market", "strategy")),
        require_edge_exceeds_spread=bool(
            _require(s, "require_edge_exceeds_spread", "strategy")
        ),
    )

    p = _require(raw, "paths", "config")
    paths = Paths(
        data_dir=_resolve(_require(p, "data_dir", "paths")),
        ensemble_dir=_resolve(_require(p, "ensemble_dir", "paths")),
        observations_dir=_resolve(_require(p, "observations_dir", "paths")),
        database=_resolve(_require(p, "database", "paths")),
    )

    return Config(
        stations=stations,
        gefs=gefs,
        kalshi=kalshi,
        strategy=strategy,
        paths=paths,
    )
