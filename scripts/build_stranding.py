"""The stranding window: when the metro still runs but the buses have stopped.

    python scripts/build_stranding.py

Writes outputs/stranding.csv and outputs/stranding.json, the latter folded into
the interactive atlas by build_atlas.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collapse import (  # noqa: E402
    MIN_USEFUL_DESTINATIONS,
    SWEEP_HOURS,
    collapse_times,
    hourly_profile,
    last_metro,
    stranding_window,
)
from src.destinations import trip_destinations  # noqa: E402
from src.gtfs_repair import rebuild_times  # noqa: E402
from src.pipeline import Feeds  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    print("loading feeds...")
    feeds = Feeds(ROOT)

    stop_times = rebuild_times(feeds.bus_stop_times, feeds.bus_stops)
    trip_dests = trip_destinations(feeds.bus_trips).dropna(subset=["destination"])

    print("walking the evening hour by hour...")
    profile = hourly_profile(feeds.pairs, stop_times, trip_dests)
    collapse = collapse_times(profile, feeds.stations)

    train = last_metro(feeds.metro_stop_times, feeds.metro_stops, feeds.metro_trips)
    result = stranding_window(collapse, train)

    coords = feeds.stations.set_index("stop_id")[["stop_lat", "stop_lon"]]
    result["lat"] = result["station_id"].map(coords["stop_lat"])
    result["lon"] = result["station_id"].map(coords["stop_lon"])

    csv_cols = [
        "station_name", "peak_destinations", "dark_from", "last_metro",
        "stranded", "stranded_hours", "half_reach_from",
    ]
    result[csv_cols].to_csv(OUT / "stranding.csv", index=False)
    print(f"  wrote outputs/stranding.csv")

    payload = {
        "hours": SWEEP_HOURS,
        "threshold": MIN_USEFUL_DESTINATIONS,
        "stations": [
            {
                "id": r.station_id,
                "name": r.station_name,
                "lat": float(r.lat),
                "lon": float(r.lon),
                "curve": r.curve,
                "peak": int(r.peak_destinations),
                "dark_from": r.dark_from,
                "dark_hour": (
                    float(r.dark_from_hour) if pd.notna(r.dark_from_hour) else None
                ),
                "last_metro": r.last_metro,
                "last_metro_hour": (
                    float(r.last_metro_hour) if pd.notna(r.last_metro_hour) else None
                ),
                "stranded": r.stranded,
                "stranded_hours": (
                    round(float(r.stranded_hours), 3)
                    if pd.notna(r.stranded_hours)
                    else None
                ),
            }
            for r in result.itertuples()
        ],
    }
    (OUT / "stranding.json").write_text(json.dumps(payload), encoding="utf-8")
    print(f"  wrote outputs/stranding.json")

    stranded = result[result["stranded_hours"] > 0]
    print(
        f"\n{len(stranded)} of {len(result)} stations run metro trains after their"
        f"\nbus network has already gone dark (fewer than {MIN_USEFUL_DESTINATIONS}"
        f" destinations reachable).\n"
    )
    print(
        result.head(10)[
            ["station_name", "dark_from", "last_metro", "stranded", "peak_destinations"]
        ].to_string(index=False)
    )
    print(
        f"\nmedian stranding window: "
        f"{result['stranded_hours'].median():.2f} h"
    )


if __name__ == "__main__":
    main()
