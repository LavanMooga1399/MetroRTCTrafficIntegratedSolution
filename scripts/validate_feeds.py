"""Pre-event validation pass over both GTFS feeds.

Run this before trusting any analysis. It reports row counts, coordinate
validity, service calendars and the post-midnight time encoding, and it logs
bad records rather than silently dropping them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gtfs_loader import (  # noqa: E402
    HYDERABAD_BBOX,
    gtfs_time_to_seconds,
    load_feed,
    metro_stations,
    normalise_stops,
    seconds_to_clock,
)

ROOT = Path(__file__).resolve().parents[1]


def report(name: str, feed_dir: Path) -> None:
    print(f"\n{'=' * 70}\n{name}  ({feed_dir.relative_to(ROOT)})\n{'=' * 70}")
    feed = load_feed(feed_dir)

    print("\n-- tables --")
    for table, df in feed.items():
        print(f"  {table:<18} {len(df):>9,} rows   {len(df.columns)} cols")

    missing = {"agency", "stops", "routes", "trips", "stop_times"} - feed.keys()
    print(f"  required tables missing: {sorted(missing) or 'none'}")
    print(f"  optional absent: {sorted({'shapes','calendar_dates','calendar'} - feed.keys())}")

    stops = normalise_stops(feed["stops"])
    bad = stops[~stops["coord_ok"]]
    print("\n-- coordinates --")
    print(f"  stops: {len(stops):,}")
    print(f"  null lat/lon: {int(stops[['stop_lat','stop_lon']].isna().any(axis=1).sum()):,}")
    print(f"  outside Hyderabad bbox: {len(bad):,}")
    if not bad.empty:
        print(bad[["stop_id", "stop_name", "stop_lat", "stop_lon"]].head(10).to_string(index=False))
    ok = stops[stops["coord_ok"]]
    print(f"  lat range: {ok['stop_lat'].min():.5f} .. {ok['stop_lat'].max():.5f}")
    print(f"  lon range: {ok['stop_lon'].min():.5f} .. {ok['stop_lon'].max():.5f}")
    print(f"  (bbox sanity: lat must be ~17.x, lon ~78.x — swapped feeds fail here)")

    if "calendar" in feed:
        print("\n-- calendar --")
        print(feed["calendar"].to_string(index=False))
    if "calendar_dates" in feed:
        print(f"  calendar_dates rows: {len(feed['calendar_dates']):,}")

    st = feed["stop_times"]
    dep = gtfs_time_to_seconds(st["departure_time"])
    print("\n-- times --")
    print(f"  stop_times rows: {len(st):,}")
    print(f"  unparseable departure_time: {int(dep.isna().sum()):,}")
    print(f"  earliest: {seconds_to_clock(dep.min())}   latest: {seconds_to_clock(dep.max())}")
    past_midnight = int((dep >= 24 * 3600).sum())
    print(f"  departures at 24:00:00 or later: {past_midnight:,}")
    if past_midnight:
        sample = st.loc[dep >= 24 * 3600, "departure_time"].drop_duplicates().head(5).tolist()
        print(f"  sample encodings: {sample}")

    trips = feed["trips"]
    print("\n-- service --")
    print(f"  routes: {len(feed['routes']):,}   trips: {len(trips):,}")
    print(f"  service_ids in trips: {sorted(trips['service_id'].dropna().unique())[:10]}")
    orphan_trips = set(st["trip_id"]) - set(trips["trip_id"])
    orphan_stops = set(st["stop_id"]) - set(stops["stop_id"])
    print(f"  stop_times trip_ids not in trips.txt: {len(orphan_trips):,}")
    print(f"  stop_times stop_ids not in stops.txt: {len(orphan_stops):,}")

    if "location_type" in stops.columns:
        stations = metro_stations(feed["stops"])
        print(f"\n-- stations --\n  parent stations: {len(stations):,}")
        print(f"  platform-level stops: {int((stops['location_type'] == '0').sum()):,}")


def main() -> None:
    report("TGSRTC (bus)", ROOT / "data" / "tgsrtc")
    report("HMRL (metro)", ROOT / "data" / "hmrl")
    print(f"\nbbox used for validation: {HYDERABAD_BBOX}")


if __name__ == "__main__":
    main()
