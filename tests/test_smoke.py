"""Phase 0 smoke test: the config layer parses config.yaml correctly.

Real domain logic (GEFS, fair value, backtest) gets its own tests in later
phases.
"""

from src.common.config import Config, ConfigError, load_config


def test_config_loads():
    """config.yaml parses into a Config without error."""
    config = load_config()
    assert isinstance(config, Config)


def test_stations_present_and_unique():
    """All 20 configured stations load with unique ids."""
    config = load_config()
    assert len(config.stations) == 20
    ids = [s.id for s in config.stations]
    assert len(ids) == len(set(ids)), "station ids must be unique"


def test_station_coords_in_range():
    """Every station has plausible lat/lon."""
    config = load_config()
    for s in config.stations:
        assert -90.0 <= s.latitude <= 90.0
        assert -180.0 <= s.longitude <= 180.0


def test_resolution_unverified_in_phase0():
    """Resolution stations are intentionally blank until Phase 2 verifies them."""
    config = load_config()
    assert all(not s.resolution_verified for s in config.stations)


def test_station_lookup():
    """Config.station() finds a known station and rejects an unknown one."""
    config = load_config()
    assert config.station("KNYC").name == "New York City"
    try:
        config.station("KXXX")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown station id")


def test_missing_file_raises():
    """A missing config path raises ConfigError, not a bare OSError."""
    try:
        load_config("does/not/exist.yaml")
    except ConfigError:
        pass
    else:
        raise AssertionError("expected ConfigError for missing file")
