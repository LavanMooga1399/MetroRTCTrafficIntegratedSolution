"""Where do the buses at a metro station actually go?

The count of routes near a station is misleading on its own: Secunderabad East
has 341 route_ids within 500 m, which says a bus will come but not that a
useful bus will come. Properly separating useful from merely present needs
origin-destination data, which the open feeds do not carry.

They do, however, carry something usable in the meantime. 79% of TGSRTC
`trip_short_name` values encode their terminals in the form
"219-SILVER ROUTE-SECUNDERABAD-TO-PATANCHERUVU", so the destination of each
trip can be parsed directly out of the feed. Counting DISTINCT destinations
reachable from a station, rather than distinct route_ids, gives a first-order
measure of how much of the city a transfer there actually opens up -- and it
degrades honestly at night, when the routes that keep running tend to be the
few trunk corridors rather than a spread of destinations.

This is a proxy, not demand data. It measures reach, not whether anyone wants
to go there.
"""

from __future__ import annotations

import re

import pandas as pd

# The terminal pair, with an optional depot suffix ("-HYT2", "-MP") discarded.
_OD_PATTERN = re.compile(
    r"-(?P<origin>[A-Z0-9 .()/&-]+?)-TO-(?P<dest>[A-Z0-9 .()/&-]+?)(?:-[A-Z]{2,4}\d?)?$",
    re.IGNORECASE,
)

# The feed spells several major terminals more than one way; collapsing them
# stops one place being counted as two destinations.
_ALIASES = {
    "SECUNDRABAD": "SECUNDERABAD",
    "SECUNDERABAD DEPOT": "SECUNDERABAD",
    "UPPAL X ROAD": "UPPAL",
    "UPPAL DEPOT": "UPPAL",
    "JBS": "JUBILEE BUS STATION",
    "CBS": "CENTRAL BUS STATION",
    "MGBS": "MAHATMA GANDHI BUS STATION",
    "PATANCHERUVU": "PATANCHERU",
}


def normalise_place(name: pd.Series) -> pd.Series:
    """Upper-case, squeeze whitespace, drop a trailing DEPOT, apply aliases."""
    cleaned = (
        name.astype("string")
        .str.upper()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.replace(r"\s+DEPOT\d*$", "", regex=True)
    )
    return cleaned.replace(_ALIASES)


def trip_destinations(trips: pd.DataFrame) -> pd.DataFrame:
    """Extract origin and destination terminals from trip_short_name."""
    parsed = trips["trip_short_name"].str.extract(_OD_PATTERN)
    out = trips[["trip_id", "route_id"]].copy()
    out["origin"] = normalise_place(parsed["origin"])
    out["destination"] = normalise_place(parsed["dest"])
    return out


def destination_reach(
    pairs: pd.DataFrame,
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    band_col: str = "band",
    time_col: str = "departure_time_s_est",
) -> pd.DataFrame:
    """Distinct bus destinations reachable from each station, by band and radius.

    `pairs` is spatial.stops_near_stations output; `stop_times` must already
    carry a band label from connectivity.band_of.
    """
    dests = trip_destinations(trips).dropna(subset=["destination"])
    st = stop_times[["trip_id", "stop_id", band_col]].merge(dests, on="trip_id")
    st = st.dropna(subset=[band_col])

    merged = pairs[["station_id", "radius_m", "bus_stop_id"]].merge(
        st, left_on="bus_stop_id", right_on="stop_id", how="inner"
    )
    return (
        merged.groupby(["station_id", "radius_m", band_col], observed=True)
        .agg(
            n_destinations=("destination", "nunique"),
            n_routes_named=("route_id", "nunique"),
            n_trips=("trip_id", "nunique"),
        )
        .reset_index()
    )
