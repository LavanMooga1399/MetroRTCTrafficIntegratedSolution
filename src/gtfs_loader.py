"""GTFS loading and normalisation for the TGSRTC and HMRL feeds.

Both feeds come from Open Data Telangana and are close to the spec, but they
differ in ways that matter downstream:

  * TGSRTC has no shapes.txt and a single always-on service_id, so there is no
    weekday/weekend distinction to select on.
  * HMRL models platforms as child stops (location_type 0) under a station
    parent (location_type 1), so station-level work must dedupe to parents.
  * GTFS encodes trips running past midnight as 24:xx:xx, 25:xx:xx and so on.
    pandas will not parse those as times, and they are exactly the trips the
    late-night analysis depends on, so they are kept as integer seconds.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Generous bounding box around Greater Hyderabad, used to catch swapped or
# null coordinates rather than to clip the network.
HYDERABAD_BBOX = {"min_lat": 16.8, "max_lat": 18.2, "min_lon": 77.8, "max_lon": 79.2}

# UTM zone 44N. Buffers must be taken in metres, never in WGS84 degrees.
METRIC_CRS = "EPSG:32644"
WGS84 = "EPSG:4326"


def gtfs_time_to_seconds(series: pd.Series) -> pd.Series:
    """Convert GTFS HH:MM:SS to seconds after midnight, preserving hours >= 24."""
    parts = series.astype("string").str.strip().str.split(":", expand=True)
    hours = pd.to_numeric(parts[0], errors="coerce")
    minutes = pd.to_numeric(parts[1], errors="coerce")
    seconds = pd.to_numeric(parts[2], errors="coerce")
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_clock(seconds: float) -> str:
    """Inverse of gtfs_time_to_seconds, keeping the 24+ hour convention."""
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def load_feed(feed_dir: str | Path) -> dict[str, pd.DataFrame]:
    """Load every .txt table in a GTFS directory into a dict of DataFrames."""
    feed_dir = Path(feed_dir)
    feed: dict[str, pd.DataFrame] = {}
    for path in sorted(feed_dir.glob("*.txt")):
        feed[path.stem] = pd.read_csv(path, dtype="string", low_memory=False)
    return feed


def normalise_stops(stops: pd.DataFrame) -> pd.DataFrame:
    """Coerce stop coordinates to float and flag records outside Hyderabad."""
    stops = stops.copy()
    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    stops["coord_ok"] = (
        stops["stop_lat"].between(HYDERABAD_BBOX["min_lat"], HYDERABAD_BBOX["max_lat"])
        & stops["stop_lon"].between(HYDERABAD_BBOX["min_lon"], HYDERABAD_BBOX["max_lon"])
    )
    return stops


def normalise_stop_times(stop_times: pd.DataFrame) -> pd.DataFrame:
    """Add integer-second departure/arrival columns to stop_times."""
    stop_times = stop_times.copy()
    for col in ("arrival_time", "departure_time"):
        if col in stop_times.columns:
            stop_times[f"{col}_s"] = gtfs_time_to_seconds(stop_times[col])
    stop_times["stop_sequence"] = pd.to_numeric(
        stop_times["stop_sequence"], errors="coerce"
    )
    return stop_times


def metro_stations(stops: pd.DataFrame) -> pd.DataFrame:
    """Reduce the HMRL stop table to one row per station.

    Platform rows (location_type 0 with a parent_station) collapse into their
    parent. Feeds that omit location_type fall back to all stops.
    """
    stops = normalise_stops(stops)
    if "location_type" not in stops.columns:
        return stops
    parents = stops[stops["location_type"].fillna("0") == "1"]
    if not parents.empty:
        return parents.reset_index(drop=True)
    return stops[stops["parent_station"].isna()].reset_index(drop=True)
