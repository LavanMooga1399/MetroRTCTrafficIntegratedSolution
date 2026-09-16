"""Spatial join between metro stations and bus stops.

Buffers are taken in UTM zone 44N (EPSG:32644), never in WGS84 degrees: at
Hyderabad's latitude one degree of longitude is about 106 km, so buffering in
degrees would distort every radius.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from .gtfs_loader import METRIC_CRS, WGS84

# 300m comfortable, 500m the standard planning threshold, 800m the usual upper
# limit of willingness to walk to a bus.
WALK_RADII_M = (300, 500, 800)


def to_geodataframe(stops: pd.DataFrame) -> gpd.GeoDataFrame:
    """Build a metric-CRS GeoDataFrame from a stop table with lat/lon columns."""
    gdf = gpd.GeoDataFrame(
        stops.copy(),
        geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]),
        crs=WGS84,
    )
    return gdf.to_crs(METRIC_CRS)


def cluster_bus_stops(bus_stops: gpd.GeoDataFrame, tolerance_m: float = 50.0) -> gpd.GeoDataFrame:
    """Group stop records that sit within `tolerance_m` of each other.

    Bus stops on opposite sides of a road are separate GTFS records. Counting
    both inflates apparent coverage, so they are assigned a shared cluster id
    that downstream counts use instead of stop_id.
    """
    bus_stops = bus_stops.copy()
    joined = gpd.sjoin_nearest(
        bus_stops[["geometry"]],
        bus_stops[["geometry"]],
        max_distance=tolerance_m,
        distance_col="d",
    )
    # Union-find over the nearest-neighbour pairs.
    parent = {i: i for i in bus_stops.index}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for left, right in zip(joined.index, joined["index_right"]):
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    bus_stops["cluster_id"] = [find(i) for i in bus_stops.index]
    return bus_stops


def stops_near_stations(
    stations: gpd.GeoDataFrame,
    bus_stops: gpd.GeoDataFrame,
    radius_m: float,
) -> pd.DataFrame:
    """Every (station, bus stop) pair within `radius_m` straight-line metres.

    Straight-line, not street-network: a 300 m buffer can be a 600 m walk. This
    is a stated limitation, not an oversight.
    """
    buffered = stations.copy()
    buffered["geometry"] = buffered.geometry.buffer(radius_m)
    pairs = gpd.sjoin(bus_stops, buffered, predicate="within", how="inner")
    pairs = pairs.rename(columns={"stop_id_left": "bus_stop_id", "stop_id_right": "station_id"})
    stn_geom = stations.set_index("stop_id").geometry
    pairs["walk_m"] = [
        geom.distance(stn_geom.loc[sid])
        for geom, sid in zip(pairs.geometry, pairs["station_id"])
    ]
    pairs["radius_m"] = radius_m
    return pd.DataFrame(pairs.drop(columns="geometry"))
