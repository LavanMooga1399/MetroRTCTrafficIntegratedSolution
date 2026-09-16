"""Solve the late-night allocation at several budgets and export it for the map.

    python scripts/export_optimisation.py

Writes outputs/optimisation.json, which scripts/build_atlas.py folds into the
interactive atlas. Kept separate from build_atlas because the GA takes minutes
and the map should rebuild in seconds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.connectivity import band_of  # noqa: E402
from src.destinations import trip_destinations  # noqa: E402
from src.gtfs_repair import rebuild_times  # noqa: E402
from src.optimise import LateNightOptimiser, build_candidates  # noqa: E402
from src.pipeline import Feeds  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"

TARGET_STATIONS = [
    "Raidurg",
    "Gandhi Hospital",
    "Musheerabad",
    "RTC Cross Roads",
    "Bharat Nagar",
    "Erragadda",
]
BUDGETS = [10, 20, 30, 50, 100]


def route_paths(
    route_ids: set[str],
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    stops: pd.DataFrame,
) -> dict[str, list[list[float]]]:
    """A drawable alignment per route.

    TGSRTC ships no shapes.txt, so the line is the stop sequence of that
    route's longest trip joined straight -- the real alignment follows roads,
    but for showing WHERE a restarted route runs this is honest and adequate.
    """
    coords = stops.set_index("stop_id")[["stop_lat", "stop_lon"]]
    wanted = trips[trips["route_id"].isin(route_ids)][["trip_id", "route_id"]]
    st = stop_times.merge(wanted, on="trip_id", how="inner")

    paths: dict[str, list[list[float]]] = {}
    for route_id, group in st.groupby("route_id"):
        longest = group["trip_id"].value_counts().idxmax()
        seq = group[group["trip_id"] == longest].sort_values("stop_sequence")
        pts = coords.reindex(seq["stop_id"]).dropna()
        paths[str(route_id)] = [[float(a), float(b)] for a, b in pts.to_numpy()]
    return paths


def main() -> None:
    OUT.mkdir(exist_ok=True)
    print("loading feeds...")
    feeds = Feeds(ROOT)

    stop_times = rebuild_times(feeds.bus_stop_times, feeds.bus_stops)
    stop_times["band"] = band_of(stop_times["departure_time_s_est"])
    trip_dests = trip_destinations(feeds.bus_trips).dropna(subset=["destination"])
    names = feeds.stations.set_index("stop_id")["stop_name"]

    candidates, already = build_candidates(
        feeds.pairs, stop_times, trip_dests, names, TARGET_STATIONS
    )
    print(f"{len(candidates)} candidate routes\n")

    solutions: dict[str, dict] = {}
    all_routes: set[str] = set()

    for budget in BUDGETS:
        opt = LateNightOptimiser(candidates, budget_hours=budget)
        greedy = opt.greedy()
        best, _ = opt.run()
        chosen = [opt.candidates[i] for i in best.selected.nonzero()[0]]
        all_routes |= {c.route_id for c in chosen}

        entry = {
            "budget": budget,
            "connections": best.new_pairs,
            "greedy": greedy.new_pairs,
            "hours_used": round(best.cost_hours, 1),
            "per_hour": round(best.new_pairs / max(best.cost_hours, 1e-9), 2),
            "stations_reconnected": best.stations_reconnected,
            "per_station": best.per_station,
            "routes": [
                {
                    "route_id": c.route_id,
                    "hours": c.cost_hours,
                    "stations": list(c.stations),
                    "destinations_added": sum(len(d) for d in c.gains.values()),
                }
                for c in sorted(
                    chosen,
                    key=lambda c: -sum(len(d) for d in c.gains.values()),
                )
            ],
        }

        # The equity variant: force the abandoned station's best service in and
        # re-optimise the remainder, so the trade-off can be toggled on the map
        # rather than argued about in the abstract.
        abandoned = [s for s in TARGET_STATIONS if best.per_station.get(s, 0) == 0]
        if abandoned:
            station = abandoned[0]
            options = [(i, c) for i, c in enumerate(candidates) if station in c.gains]
            idx, forced = max(
                options, key=lambda x: len(x[1].gains[station]) / x[1].cost_hours
            )
            remainder = [c for j, c in enumerate(candidates) if j != idx]
            rest, _ = LateNightOptimiser(
                remainder, budget - forced.cost_hours
            ).run()
            rest_chosen = [
                remainder[i] for i in rest.selected.nonzero()[0]
            ]
            all_routes |= {c.route_id for c in rest_chosen} | {forced.route_id}
            per_station = dict(rest.per_station)
            for s, d in forced.gains.items():
                per_station[s] = per_station.get(s, 0) + len(d)
            total = rest.new_pairs + sum(len(d) for d in forced.gains.values())
            entry["equity"] = {
                "station": station,
                "forced_route": forced.route_id,
                "forced_hours": forced.cost_hours,
                "station_gain": len(forced.gains[station]),
                "connections": total,
                "cost": best.new_pairs - total,
                "per_station": per_station,
                "routes": [
                    {
                        "route_id": c.route_id,
                        "hours": c.cost_hours,
                        "stations": list(c.stations),
                        "destinations_added": sum(len(d) for d in c.gains.values()),
                        "forced": c.route_id == forced.route_id,
                    }
                    for c in sorted(
                        rest_chosen + [forced],
                        key=lambda c: -sum(len(d) for d in c.gains.values()),
                    )
                ],
            }

        solutions[str(budget)] = entry
        print(
            f"  budget {budget:>4}h | GA {best.new_pairs:>3} (greedy {greedy.new_pairs:>3})"
            f" | {len(chosen)} routes"
            + (
                f" | equity: {entry['equity']['station']} +{entry['equity']['station_gain']}"
                f" costs {entry['equity']['cost']}"
                if "equity" in entry
                else ""
            )
        )

    print(f"\nextracting alignments for {len(all_routes)} routes...")
    paths = route_paths(all_routes, stop_times, feeds.bus_trips, feeds.bus_stops)

    station_coords = feeds.stations.set_index("stop_id")[["stop_lat", "stop_lon"]]
    payload = {
        "budgets": BUDGETS,
        "solutions": solutions,
        "paths": paths,
        "targets": {
            s: {
                "reachable_now": len(already[s]),
                "lat": float(
                    station_coords.loc[names[names == s].index[0], "stop_lat"]
                ),
                "lon": float(
                    station_coords.loc[names[names == s].index[0], "stop_lon"]
                ),
            }
            for s in TARGET_STATIONS
        },
    }
    path = OUT / "optimisation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
