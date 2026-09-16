"""Repair for the defective departure times in the TGSRTC feed.

THE DEFECT
----------
In the 08 February 2026 TGSRTC feed, the gap between consecutive stop_times
grows linearly with stop_sequence instead of tracking distance:

    corr(gap, stop_sequence) = 0.88
    corr(gap, distance)      = 0.06

The median gap between stops is ~6 min at sequence 1-5 but ~121 min at
sequence 60-90, while the median distance between stops stays flat at about
0.72 km throughout. The implied bus speed therefore decays from 8.1 km/h to
0.44 km/h -- slower than walking -- purely as a function of how many stops the
bus has already passed. The longest "trip" is route 300 Mehdipatnam to JBS, an
ordinary city route, encoded as 303 hours.

The gap is almost exactly proportional to the sequence index:
    gap_n / n has median 2.13 min and an IQR of 1.79-2.49 min.

So the feed generator appears to multiply each increment by the stop index.

CONSEQUENCE
-----------
Departure times are usable at the START of a trip and progressively useless
afterwards. Any frequency or headway computed at a mid-route bus stop from the
published times is wrong, and wrong by hours. Since a metro station is almost
never the first stop of a bus trip, this defect hits the Transfer Gap Atlas
directly and must be corrected rather than worked around.

THE CORRECTION
--------------
Trip start times are sound -- their distribution peaks at 06:00-08:00 and
14:00-19:00 and tapers to near zero after 23:00, which is what a real bus
network looks like. So we keep the first departure of each trip and re-derive
every subsequent stop time by propagating along the route at a calibrated
speed, using straight-line distance between consecutive stops scaled by a
circuity factor to approximate road distance.

Two independent routes to the speed constant agree, which is the main reason
to trust it:
  * dividing each published gap by its sequence index implies ~20.8 km/h
    median, and flat across the whole trip
  * that figure is consistent with the TomTom 2025 Hyderabad rush-hour mean of
    16.1 km/h and 34.1 km/h on highways

Reconstructed times are ESTIMATES. They are labelled as such in the output and
the limitation must be stated in any presentation of the results.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088

# Median implied speed once the sequence multiplier is divided out.
DEFAULT_SPEED_KMPH = 20.0
# Straight line under-states road distance; 1.3 is the standard planning value.
CIRCUITY_FACTOR = 1.3
# Dwell at each stop, added on top of running time.
DWELL_SECONDS = 20.0


def haversine_km(
    lat1: pd.Series, lon1: pd.Series, lat2: pd.Series, lon2: pd.Series
) -> pd.Series:
    """Great-circle distance in km between two coordinate series."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * np.arcsin(np.sqrt(a))


def diagnose(stop_times: pd.DataFrame, stops: pd.DataFrame) -> dict[str, float]:
    """Measure the defect. Returns the two correlations that characterise it."""
    st = _with_geometry(stop_times, stops)
    st["gap_s"] = st.groupby("trip_id")["departure_time_s"].diff()
    valid = st.dropna(subset=["gap_s", "dist_km"])
    valid = valid[valid["dist_km"] > 0.05]
    return {
        "corr_gap_sequence": float(valid["gap_s"].corr(valid["stop_sequence"])),
        "corr_gap_distance": float(valid["gap_s"].corr(valid["dist_km"])),
        "median_gap_min": float(valid["gap_s"].median() / 60),
        "median_dist_km": float(valid["dist_km"].median()),
        "implied_speed_kmph": float(
            (valid["dist_km"] / (valid["gap_s"] / 3600)).median()
        ),
        "max_trip_hours": float(
            st.groupby("trip_id")["departure_time_s"].agg(lambda s: s.max() - s.min()).max()
            / 3600
        ),
    }


def _with_geometry(stop_times: pd.DataFrame, stops: pd.DataFrame) -> pd.DataFrame:
    """Attach coordinates and the distance from the previous stop on the trip."""
    coords = stops.set_index("stop_id")[["stop_lat", "stop_lon"]]
    st = stop_times.sort_values(["trip_id", "stop_sequence"]).copy()
    st["lat"] = st["stop_id"].map(coords["stop_lat"])
    st["lon"] = st["stop_id"].map(coords["stop_lon"])
    st["dist_km"] = haversine_km(
        st.groupby("trip_id")["lat"].shift(),
        st.groupby("trip_id")["lon"].shift(),
        st["lat"],
        st["lon"],
    )
    return st


def rebuild_times(
    stop_times: pd.DataFrame,
    stops: pd.DataFrame,
    speed_kmph: float = DEFAULT_SPEED_KMPH,
    circuity: float = CIRCUITY_FACTOR,
    dwell_s: float = DWELL_SECONDS,
) -> pd.DataFrame:
    """Re-derive stop times by propagating each trip's start along its route.

    Adds `departure_time_s_est`, seconds after midnight on the service day,
    preserving the GTFS convention that a trip crossing midnight exceeds 86400.
    """
    st = _with_geometry(stop_times, stops)
    st["dist_km"] = st["dist_km"].fillna(0.0)

    run_s = (st["dist_km"] * circuity / speed_kmph) * 3600
    # First stop of each trip contributes no running time and no dwell.
    is_first = st["stop_sequence"] == st.groupby("trip_id")["stop_sequence"].transform("min")
    leg_s = run_s + dwell_s
    leg_s = leg_s.where(~is_first, 0.0)

    start_s = st.groupby("trip_id")["departure_time_s"].transform("min")
    st["departure_time_s_est"] = start_s + leg_s.groupby(st["trip_id"]).cumsum()
    st["arrival_time_s_est"] = st["departure_time_s_est"] - dwell_s
    st.loc[is_first, "arrival_time_s_est"] = st.loc[is_first, "departure_time_s_est"]
    return st
