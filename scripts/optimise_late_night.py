"""Allocate additional late-night bus-hours across the worst-connected stations.

    python scripts/optimise_late_night.py

Runs the GA at several budgets against a greedy baseline and writes the
before/after allocation table.
"""

from __future__ import annotations

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

# The six stations that stayed in the worst ten under all four repair models.
# Using only these keeps the intervention defensible: they are the stations
# whose failure does not depend on how the schedule was reconstructed.
TARGET_STATIONS = [
    "Raidurg",
    "Gandhi Hospital",
    "Musheerabad",
    "RTC Cross Roads",
    "Bharat Nagar",
    "Erragadda",
]

BUDGETS = [10, 20, 30, 50, 100]
HEADLINE_BUDGET = 30


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
    print(f"\n{len(candidates)} candidate activations across {len(TARGET_STATIONS)} stations")
    print(f"search space: 2^{len(candidates)} packages\n")

    per_station = pd.DataFrame(
        [
            {
                "station": s,
                "candidate_routes": sum(1 for c in candidates if s in c.gains),
                "reachable_now": len(already[s]),
                "dormant_destinations": len(
                    set().union(*[c.gains[s] for c in candidates if s in c.gains])
                ),
            }
            for s in TARGET_STATIONS
        ]
    )
    print(per_station.to_string(index=False))

    rows = []
    headline = None
    for budget in BUDGETS:
        opt = LateNightOptimiser(candidates, budget_hours=budget)
        greedy = opt.greedy()
        best, history = opt.run()
        lift = (best.new_pairs - greedy.new_pairs) / max(greedy.new_pairs, 1)
        print(
            f"\nbudget {budget:>4} bus-h | greedy {greedy.new_pairs:>3} pairs"
            f" | GA {best.new_pairs:>3} pairs ({lift:+.1%})"
            f" | used {best.cost_hours:.1f} h"
            f" | stations reconnected {best.stations_reconnected}/{len(TARGET_STATIONS)}"
            f" | converged gen {history.index(max(history)) + 1}"
        )
        rows.append(
            {
                "budget_hours": budget,
                "greedy_new_pairs": greedy.new_pairs,
                "ga_new_pairs": best.new_pairs,
                "ga_lift": round(lift, 4),
                "hours_used": round(best.cost_hours, 1),
                "stations_reconnected": best.stations_reconnected,
                **{f"gain_{s}": best.per_station.get(s, 0) for s in TARGET_STATIONS},
            }
        )
        if budget == HEADLINE_BUDGET:
            headline = (opt, best)

    pd.DataFrame(rows).to_csv(OUT / "optimisation_budget_sweep.csv", index=False)
    print("\n  wrote outputs/optimisation_budget_sweep.csv")

    if headline is None:
        return
    opt, best = headline
    chosen = [opt.candidates[i] for i in best.selected.nonzero()[0]]
    plan = pd.DataFrame(
        [
            {
                "route_id": c.route_id,
                "bus_hours": c.cost_hours,
                "target_stations_served": len(c.gains),
                "stations": ", ".join(c.stations),
                "destinations_added": sum(len(d) for d in c.gains.values()),
            }
            for c in chosen
        ]
    ).sort_values("destinations_added", ascending=False)
    plan.to_csv(OUT / "late_night_service_plan.csv", index=False)
    print("  wrote outputs/late_night_service_plan.csv")

    print(f"\n=== recommended plan at {HEADLINE_BUDGET} additional bus-hours ===")
    print(f"  {len(plan)} routes restarted for {plan['bus_hours'].sum():.1f} bus-hours")
    print()
    print(plan.head(12).to_string(index=False))
    summary = pd.DataFrame(
        [
            {
                "station": s,
                "reachable_before": len(already[s]),
                "destinations_gained": best.per_station.get(s, 0),
            }
            for s in TARGET_STATIONS
        ]
    )
    print()
    print(summary.to_string(index=False))
    print(
        f"\n  {best.new_pairs} station-destination connections created"
        f" for {best.cost_hours:.1f} bus-hours"
        f"  ({best.new_pairs / best.cost_hours:.1f} per hour)"
    )

    abandoned = [s for s in TARGET_STATIONS if best.per_station.get(s, 0) == 0]
    for station in abandoned:
        _equity_scenario(candidates, best, station, HEADLINE_BUDGET)


def _equity_scenario(candidates, unconstrained, station: str, budget: float) -> None:
    """Price the decision the optimiser refuses to make on its own.

    Pure efficiency abandons the station whose routes are long and shared with
    nobody. Rather than fold an equity weight into the fitness -- where the
    trade-off becomes invisible and unarguable -- we force the best available
    service in and report what it costs. A planner can then decide, which is
    the correct place for that decision to sit.
    """
    options = [(i, c) for i, c in enumerate(candidates) if station in c.gains]
    if not options:
        return
    idx, forced = max(
        options, key=lambda x: len(x[1].gains[station]) / x[1].cost_hours
    )
    remainder = [c for j, c in enumerate(candidates) if j != idx]
    rest, _ = LateNightOptimiser(remainder, budget - forced.cost_hours).run()
    total = rest.new_pairs + sum(len(d) for d in forced.gains.values())
    lost = unconstrained.new_pairs - total

    print(f"\n  EQUITY: told only to maximise connections, the optimiser leaves")
    print(f"  {station} with nothing -- its routes are long and serve no other")
    print(f"  target station, so each bus-hour there buys fewer connections.")
    print(f"\n  Forcing it back in, at the same {budget:g} bus-hour budget:")
    print(f"    restart route {forced.route_id} ({forced.cost_hours:.1f} bus-hours)")
    print(f"    {station} gains {len(forced.gains[station])} destinations, 0 -> "
          f"{len(forced.gains[station])}")
    print(f"    total connections {unconstrained.new_pairs} -> {total}"
          f"  (cost: {lost}, {lost / max(unconstrained.new_pairs, 1):.1%})")
    print(f"\n  Reconnecting {station} costs about {lost / max(unconstrained.new_pairs, 1):.0%}"
          f" of the network-wide gain.")
    print("  That is a policy choice, not a mathematical one, which is why it is")
    print("  reported here instead of buried in a fitness weight.")


if __name__ == "__main__":
    main()
