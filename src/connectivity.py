"""The Transfer Gap Score.

The thesis: a unified ticket does not create a connection. A single ticket
valid on Metro and bus is worthless to someone who alights at a metro station
at 21:00 and finds no bus within walking distance. Integration is only as good
as the worst transfer in the chain, and nobody has measured Hyderabad's.

For each (station, time band, walk radius) we compute how long someone leaving
the metro would expect to wait for any bus at any stop inside the radius.

Under random passenger arrival the expected wait for a single route of headway
h is h/2. For n independent routes the combined expected wait is

    E[wait] = 1 / (2 * sum(1 / h_i))

which is why a station served by four half-hourly routes is far better than a
station served by one half-hourly route, even though both have "a bus every
30 minutes" on paper.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOUR = 3600

# Bands are in seconds after midnight; the late band runs past 24:00 because
# GTFS encodes post-midnight trips as 24:xx and later.
TIME_BANDS: dict[str, tuple[int, int]] = {
    "morning_peak": (7 * HOUR, 10 * HOUR),
    "midday": (10 * HOUR, 16 * HOUR),
    "evening_peak": (16 * HOUR, 20 * HOUR),
    "night": (20 * HOUR, 23 * HOUR),
    "late_night": (23 * HOUR, 28 * HOUR),
}

# Wait beyond this is treated as no usable service rather than a long wait.
NO_SERVICE_WAIT_MIN = 60.0

# A caveat on expected_wait_min, which saturates in Hyderabad.
#
# TGSRTC bundles many service variants under a single route_id -- route 219
# alone carries 2,087 trips a day across the Secunderabad-Patancheruvu-Isnapur
# corridor -- and a major interchange such as Secunderabad East sits inside
# 500 m of 341 distinct route_ids. The combined-wait formula then returns a few
# seconds for almost every station in daylight hours, which is arithmetically
# right and analytically useless: it says a bus will come, not that a useful
# bus will come. Distinguishing useful from merely present needs
# origin-destination data, which the open feeds do not contain.
#
# So expected_wait_min is reported but is not the headline. The metric that
# discriminates, and that matches the thesis, is temporal: how much of a
# station's bus service survives into the evening and night. A station whose
# departures fall by 99% after 20:00 has a transfer that dies whatever the
# ticket says.
RETENTION_BASELINE_BAND = "evening_peak"


def band_of(seconds: pd.Series) -> pd.Series:
    """Label each time with its band; times outside every band become NA."""
    out = pd.Series(pd.NA, index=seconds.index, dtype="object")
    for name, (start, end) in TIME_BANDS.items():
        out = out.mask(seconds.between(start, end, inclusive="left"), name)
    return out


def route_departures(
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    time_col: str = "departure_time_s_est",
) -> pd.DataFrame:
    """Count departures per (stop, route, band) and derive the headway."""
    st = stop_times[["trip_id", "stop_id", time_col]].copy()
    st = st.merge(trips[["trip_id", "route_id"]], on="trip_id", how="inner")
    st["band"] = band_of(st[time_col])
    st = st.dropna(subset=["band"])

    counts = (
        st.groupby(["stop_id", "route_id", "band"], observed=True)
        .size()
        .reset_index(name="departures")
    )
    span_h = {k: (v[1] - v[0]) / HOUR for k, v in TIME_BANDS.items()}
    counts["band_hours"] = counts["band"].map(span_h)
    # Headway in minutes: how long between consecutive buses on this route.
    counts["headway_min"] = counts["band_hours"] * 60 / counts["departures"]
    return counts


def transfer_gap_score(
    pairs: pd.DataFrame,
    departures: pd.DataFrame,
    stop_id_col: str = "bus_stop_id",
    cluster_col: str = "cluster_id",
) -> pd.DataFrame:
    """Combine nearby-stop geometry with service frequency into one score.

    `pairs` is the output of spatial.stops_near_stations; `departures` the
    output of route_departures. One row per (station, radius, band).
    """
    merged = pairs.merge(
        departures, left_on=stop_id_col, right_on="stop_id", how="inner"
    )

    # A route serving two stops inside the same buffer is still one route; keep
    # its best (shortest) headway so it is not double-counted as extra service.
    per_route = (
        merged.groupby(["station_id", "radius_m", "band", "route_id"], observed=True)
        .agg(
            headway_min=("headway_min", "min"),
            walk_m=("walk_m", "min"),
            departures=("departures", "max"),
        )
        .reset_index()
    )

    def combine(group: pd.DataFrame) -> pd.Series:
        headways = group["headway_min"].to_numpy(dtype=float)
        headways = headways[headways > 0]
        combined_wait = (
            1.0 / (2.0 * np.sum(1.0 / headways)) if headways.size else np.inf
        )
        return pd.Series(
            {
                "n_routes": int(group["route_id"].nunique()),
                "n_departures": int(group["departures"].sum()),
                "best_headway_min": float(headways.min()) if headways.size else np.nan,
                "expected_wait_min": float(combined_wait),
                "nearest_bus_stop_m": float(group["walk_m"].min()),
            }
        )

    scored = (
        per_route.groupby(["station_id", "radius_m", "band"], observed=True)
        .apply(combine, include_groups=False)
        .reset_index()
    )
    span_h = {k: (v[1] - v[0]) / HOUR for k, v in TIME_BANDS.items()}
    scored["departures_per_hour"] = scored["n_departures"] / scored["band"].map(span_h)
    scored["usable"] = scored["expected_wait_min"] <= NO_SERVICE_WAIT_MIN
    return add_retention(scored)


def add_retention(scored: pd.DataFrame) -> pd.DataFrame:
    """Express each band's service as a share of the station's own busiest band.

    Normalising against the station itself, rather than across stations,
    separates the question "is this station busy?" from "does its service
    survive the evening?" -- which is the one the Atlas is about.
    """
    scored = scored.copy()
    baseline = (
        scored[scored["band"] == RETENTION_BASELINE_BAND]
        .set_index(["station_id", "radius_m"])["departures_per_hour"]
    )
    key = pd.MultiIndex.from_arrays([scored["station_id"], scored["radius_m"]])
    scored["baseline_dep_per_hour"] = baseline.reindex(key).to_numpy()
    scored["retention"] = (
        scored["departures_per_hour"] / scored["baseline_dep_per_hour"]
    ).replace([np.inf, -np.inf], np.nan)
    scored["grade"] = pd.cut(
        scored["retention"],
        bins=[-np.inf, 0.01, 0.05, 0.15, 0.40, np.inf],
        labels=["no_service", "collapsed", "poor", "reduced", "maintained"],
    )
    return scored


def complete_grid(
    scored: pd.DataFrame, stations: pd.DataFrame, radii: tuple[int, ...]
) -> pd.DataFrame:
    """Add explicit no-service rows for combinations that produced no match.

    A station with no bus route inside the radius in a band drops out of the
    join entirely. Those are the most important rows in the analysis, so they
    are reinstated rather than left missing.
    """
    index = pd.MultiIndex.from_product(
        [stations["stop_id"].unique(), list(radii), list(TIME_BANDS)],
        names=["station_id", "radius_m", "band"],
    )
    full = scored.set_index(["station_id", "radius_m", "band"]).reindex(index).reset_index()
    full["n_routes"] = full["n_routes"].fillna(0).astype(int)
    full["n_departures"] = full["n_departures"].fillna(0).astype(int)
    full["expected_wait_min"] = full["expected_wait_min"].fillna(np.inf)
    full["usable"] = full["usable"].fillna(False).astype(bool)
    full["departures_per_hour"] = full["departures_per_hour"].fillna(0.0)
    full["retention"] = full["retention"].fillna(0.0)
    full["grade"] = full["grade"].astype("object").fillna("no_service")
    return full
