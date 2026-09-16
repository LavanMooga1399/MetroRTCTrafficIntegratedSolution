"""When does each station's bus network actually go dark?

The five time bands elsewhere in this project are a presentational choice.
"Late night = 23:00-04:00" is a bucket, and a planner's real question is
sharper than a bucket: *what time does the last useful bus leave from here?*

So this drops the bands and walks the evening hour by hour, finding for each
metro station the hour at which its reachable destinations fall below a share
of that station's own daytime peak and stay there. The result is a single
clock time per station -- the point after which arriving by metro strands you.

Two properties make this worth having over the banded view:

  * It is a station-level answer, not a citywide median. "Raidurg goes dark at
    21:00, Secunderabad East at 00:00" is directly actionable in a way that
    "the median station retains 1.9%" is not.
  * It needs no arbitrary geography or cost model -- only the observed shape of
    each station's own service curve, normalised against itself.

Thresholds are a judgement call and are stated rather than hidden. The test is
ABSOLUTE, not relative to each station's peak: a station is dark once fewer
than five destinations remain reachable, and it must stay below that for the
rest of the night, so one quiet hour does not trigger it.

Relative-to-peak was tried first and is wrong here. Scoring each station
against a quarter of its own peak made Secunderabad East -- 130 destinations at
its busiest -- "collapse" earlier than Raidurg, which never exceeds 18, because
a quarter of 130 is a far higher bar than a quarter of 18. A rider does not
care what their station used to manage at 6pm; they care whether anywhere is
reachable now. An absolute floor asks that question, and it discriminates: the
relative version put 43 of 57 stations on the same hour.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOUR = 3600

# The evening sweep. Runs to 27:00 because GTFS encodes post-midnight trips as
# 24:xx-27:xx, which is exactly the window this measures.
SWEEP_HOURS = list(range(16, 28))
# Hours used to establish each station's own baseline, for context only.
PEAK_HOURS = list(range(16, 20))
# Fewer reachable destinations than this and the station counts as dark.
# Five is the smallest number that still represents a choice of where to go.
MIN_USEFUL_DESTINATIONS = 5


def hourly_profile(
    pairs: pd.DataFrame,
    stop_times: pd.DataFrame,
    trip_dests: pd.DataFrame,
    radius_m: int = 500,
    time_col: str = "departure_time_s_est",
) -> pd.DataFrame:
    """Departures and distinct destinations per (station, hour)."""
    near = pairs[pairs["radius_m"] == radius_m][["station_id", "bus_stop_id"]]

    st = stop_times[["trip_id", "stop_id", time_col]].copy()
    st["hour"] = (st[time_col] // HOUR).astype("Int64")
    st = st[st["hour"].isin(SWEEP_HOURS)]
    st = st.merge(trip_dests, on="trip_id", how="inner").dropna(subset=["destination"])

    merged = near.merge(st, left_on="bus_stop_id", right_on="stop_id", how="inner")
    return (
        merged.groupby(["station_id", "hour"], observed=True)
        .agg(
            destinations=("destination", "nunique"),
            departures=("trip_id", "nunique"),
        )
        .reset_index()
    )


def _clock(hour: float | None) -> str:
    """Fractional hours to a clock time, keeping the post-midnight convention."""
    if hour is None or not np.isfinite(hour):
        return "—"
    minutes = int(round((hour % 1) * 60))
    h = int(hour) + (1 if minutes == 60 else 0)
    minutes = 0 if minutes == 60 else minutes
    return f"{h % 24:02d}:{minutes:02d}"


def _crossing(reach: pd.Series, threshold: float) -> float | None:
    """The fractional hour at which the curve last falls through `threshold`.

    Linear interpolation between the bracketing hours, so two stations that go
    dark in the same hour are still separated by where in that hour they did
    it. Without this the answer is one of twelve values and stations pile up.
    """
    dark_from = None
    for h in SWEEP_HOURS:
        if reach.get(h, 0) < threshold and all(
            reach.get(later, 0) < threshold for later in SWEEP_HOURS if later > h
        ):
            dark_from = h
            break
    if dark_from is None:
        return None
    if dark_from == SWEEP_HOURS[0]:
        return float(dark_from)

    before, after = reach.get(dark_from - 1, 0), reach.get(dark_from, 0)
    if before <= after or before < threshold:
        return float(dark_from)
    fraction = (before - threshold) / (before - after)
    return float(dark_from - 1) + min(max(fraction, 0.0), 1.0)


def collapse_times(profile: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    """The hour each station goes dark, plus the curve that got it there."""
    grid = (
        profile.set_index(["station_id", "hour"])
        .reindex(
            pd.MultiIndex.from_product(
                [stations["stop_id"].unique(), SWEEP_HOURS],
                names=["station_id", "hour"],
            )
        )
        .fillna(0)
        .reset_index()
    )

    rows = []
    for station_id, group in grid.groupby("station_id", observed=True):
        group = group.sort_values("hour")
        reach = group.set_index("hour")["destinations"]
        peak = reach.reindex(PEAK_HOURS).max()
        if not peak or peak <= 0:
            continue

        dark_hour = _crossing(reach, MIN_USEFUL_DESTINATIONS)
        # Half of the station's own evening reach: a softer, relative reading
        # kept alongside the absolute one for stations that never had much.
        half_hour = _crossing(reach, peak / 2)

        rows.append(
            {
                "station_id": station_id,
                "peak_destinations": int(peak),
                "dark_from_hour": dark_hour,
                "dark_from": _clock(dark_hour),
                "half_reach_from": _clock(half_hour),
                "curve": [int(reach.get(h, 0)) for h in SWEEP_HOURS],
            }
        )

    out = pd.DataFrame(rows)
    names = stations.set_index("stop_id")["stop_name"]
    out["station_name"] = out["station_id"].map(names)
    return out.sort_values("dark_from_hour", na_position="last").reset_index(drop=True)


def last_metro(
    metro_stop_times: pd.DataFrame,
    metro_stops: pd.DataFrame,
    metro_trips: pd.DataFrame,
    service_id: str = "WK",
) -> pd.Series:
    """Last weekday train arrival at each station, in fractional hours.

    Platform-level stops are rolled up to their parent station, since a rider
    arriving on either platform faces the same walk to the bus.
    """
    weekday = set(metro_trips[metro_trips["service_id"] == service_id]["trip_id"])
    st = metro_stop_times[metro_stop_times["trip_id"].isin(weekday)].copy()
    parent = metro_stops.set_index("stop_id")["parent_station"]
    st["station_id"] = st["stop_id"].map(parent).fillna(st["stop_id"])
    return st.groupby("station_id")["arrival_time_s"].max() / HOUR


def stranding_window(collapse: pd.DataFrame, last_train: pd.Series) -> pd.DataFrame:
    """Hours each night when the metro still runs but the buses have stopped.

    This is the measurement the whole project exists to produce. A unified
    ticket is a promise that the two networks work as one; this is the nightly
    window in which the metro will still carry you to a station that no bus can
    carry you out of. It needs both feeds, which is precisely what the
    single-operator view of either agency cannot see.
    """
    out = collapse.copy()
    out["last_metro_hour"] = out["station_id"].map(last_train)
    out["stranded_hours"] = out["last_metro_hour"] - out["dark_from_hour"]
    out["last_metro"] = out["last_metro_hour"].map(_clock)
    out["stranded"] = out["stranded_hours"].map(_duration)
    return out.sort_values("stranded_hours", ascending=False, na_position="last")


def _duration(hours: float) -> str:
    """Fractional hours as "1h 55m", carrying 60 minutes into the hour."""
    if pd.isna(hours) or hours <= 0:
        return "—"
    minutes = int(round(hours * 60))
    return f"{minutes // 60}h {minutes % 60:02d}m"
