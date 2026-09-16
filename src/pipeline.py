"""Reusable end-to-end pipeline so the repair model can be varied and compared.

build_atlas.py runs this once with the default repair. audit_repair.py runs it
several times with different assumptions to see whether the conclusions hold.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .connectivity import band_of, complete_grid, route_departures, transfer_gap_score
from .destinations import destination_reach
from .gtfs_loader import load_feed, metro_stations, normalise_stop_times, normalise_stops
from .gtfs_repair import rebuild_times
from .spatial import WALK_RADII_M, cluster_bus_stops, stops_near_stations, to_geodataframe


class Feeds:
    """Load and normalise both feeds once; reuse across repair variants."""

    def __init__(self, root: Path):
        bus = load_feed(root / "data" / "tgsrtc")
        metro = load_feed(root / "data" / "hmrl")
        self.bus_trips = bus["trips"]
        self.bus_routes = bus["routes"]
        self.bus_stops = normalise_stops(bus["stops"])
        self.bus_stop_times = normalise_stop_times(bus["stop_times"])
        self.stations = metro_stations(metro["stops"])
        self.metro_stop_times = normalise_stop_times(metro["stop_times"])
        self.metro_trips = metro["trips"]

        self._station_gdf = to_geodataframe(self.stations)
        self._bus_gdf = cluster_bus_stops(to_geodataframe(self.bus_stops))
        self.pairs = pd.concat(
            [stops_near_stations(self._station_gdf, self._bus_gdf, r) for r in WALK_RADII_M],
            ignore_index=True,
        )

    def score(self, **repair_kwargs) -> pd.DataFrame:
        """Run repair + scoring under the given assumptions."""
        st = rebuild_times(self.bus_stop_times, self.bus_stops, **repair_kwargs)
        departures = route_departures(st, self.bus_trips)
        scored = transfer_gap_score(self.pairs, departures)
        scored = complete_grid(scored, self.stations, WALK_RADII_M)

        st["band"] = band_of(st["departure_time_s_est"])
        reach = destination_reach(self.pairs, st, self.bus_trips)
        scored = scored.merge(
            reach[["station_id", "radius_m", "band", "n_destinations"]],
            on=["station_id", "radius_m", "band"],
            how="left",
        )
        scored["n_destinations"] = scored["n_destinations"].fillna(0).astype(int)

        names = self.stations.set_index("stop_id")["stop_name"]
        scored["station_name"] = scored["station_id"].map(names)
        return scored

    def score_published(self) -> pd.DataFrame:
        """Score using the feed's own (defective) times, for comparison."""
        departures = route_departures(
            self.bus_stop_times, self.bus_trips, time_col="departure_time_s"
        )
        scored = transfer_gap_score(self.pairs, departures)
        scored = complete_grid(scored, self.stations, WALK_RADII_M)
        names = self.stations.set_index("stop_id")["stop_name"]
        scored["station_name"] = scored["station_id"].map(names)
        return scored
