"""Build the Hyderabad Transfer Gap Atlas from the two GTFS feeds.

    python scripts/build_atlas.py

Writes outputs/station_connectivity.csv (the core deliverable), a ranked
summary, and a GeoJSON of stations for mapping.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.connectivity import (  # noqa: E402
    TIME_BANDS,
    complete_grid,
    route_departures,
    transfer_gap_score,
)
from src.gtfs_loader import (  # noqa: E402
    load_feed,
    metro_stations,
    normalise_stop_times,
    normalise_stops,
)
from src.gtfs_repair import diagnose, rebuild_times  # noqa: E402
from src.spatial import (  # noqa: E402
    WALK_RADII_M,
    cluster_bus_stops,
    stops_near_stations,
    to_geodataframe,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def main() -> None:
    OUT.mkdir(exist_ok=True)

    print("loading feeds...")
    bus = load_feed(ROOT / "data" / "tgsrtc")
    metro = load_feed(ROOT / "data" / "hmrl")

    bus_stops = normalise_stops(bus["stops"])
    bus_stop_times = normalise_stop_times(bus["stop_times"])
    stations = metro_stations(metro["stops"])
    print(f"  {len(bus_stops):,} bus stops, {len(stations)} metro stations")

    print("\ndiagnosing TGSRTC departure times...")
    d = diagnose(bus_stop_times, bus_stops)
    print(f"  corr(gap, stop_sequence) = {d['corr_gap_sequence']:.3f}")
    print(f"  corr(gap, distance)      = {d['corr_gap_distance']:.3f}")
    print(f"  longest encoded trip     = {d['max_trip_hours']:.0f} hours")
    print("  -> times are driven by position in trip, not geography; rebuilding")

    bus_stop_times = rebuild_times(bus_stop_times, bus_stops)

    print("\nspatial join...")
    station_gdf = to_geodataframe(stations)
    bus_gdf = cluster_bus_stops(to_geodataframe(bus_stops))
    print(f"  {bus_gdf['cluster_id'].nunique():,} bus stop clusters after 50m dedupe")

    pairs = pd.concat(
        [stops_near_stations(station_gdf, bus_gdf, r) for r in WALK_RADII_M],
        ignore_index=True,
    )
    print(f"  {len(pairs):,} (station, bus stop) pairs across radii {WALK_RADII_M}")

    print("\nscoring...")
    departures = route_departures(bus_stop_times, bus["trips"])
    scored = transfer_gap_score(pairs, departures)
    scored = complete_grid(scored, stations, WALK_RADII_M)

    names = stations.set_index("stop_id")["stop_name"]
    scored["station_name"] = scored["station_id"].map(names)
    scored = scored[
        [
            "station_id", "station_name", "radius_m", "band", "n_routes",
            "n_departures", "departures_per_hour", "retention", "grade",
            "best_headway_min", "expected_wait_min", "nearest_bus_stop_m",
            "usable",
        ]
    ].sort_values(["station_name", "radius_m", "band"])

    path = OUT / "station_connectivity.csv"
    scored.to_csv(path, index=False)
    print(f"  wrote {path.relative_to(ROOT)}  ({len(scored):,} rows)")

    # Ranked headline table at the standard 500m planning threshold.
    at500 = scored[scored["radius_m"] == 500]
    ranked = at500.pivot_table(
        index="station_name", columns="band", values="departures_per_hour", observed=True
    )
    ranked = ranked.reindex(columns=[b for b in TIME_BANDS if b in ranked.columns])
    ranked = ranked.sort_values("late_night")
    ranked.round(1).to_csv(OUT / "ranked_stations_500m.csv")
    print(f"  wrote outputs/ranked_stations_500m.csv")

    geo = to_geodataframe(stations)[["stop_id", "stop_name", "geometry"]].to_crs("EPSG:4326")
    geo = geo.merge(
        at500.pivot_table(index="station_id", columns="band",
                          values="departures_per_hour", observed=True).reset_index(),
        left_on="stop_id", right_on="station_id", how="left",
    )
    geo.to_file(OUT / "stations.geojson", driver="GeoJSON")
    print(f"  wrote outputs/stations.geojson")

    print("\n=== headline ===")
    for band in TIME_BANDS:
        b = at500[at500["band"] == band]
        if b.empty:
            continue
        dead = int((b["departures_per_hour"] == 0).sum())
        print(
            f"  {band:<14} median {b['departures_per_hour'].median():>7.1f} bus dep/h "
            f"within 500m | retention {b['retention'].median():>5.1%} | "
            f"{dead} stations with no bus at all"
        )
    worst = at500[at500["band"] == "late_night"].nsmallest(8, "departures_per_hour")
    print("\n=== worst 8 stations, late night (23:00-04:00), 500m ===")
    print(worst[["station_name", "departures_per_hour", "retention", "grade"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
