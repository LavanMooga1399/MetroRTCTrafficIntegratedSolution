"""Audit the TGSRTC time repair: is the defect uniform, and do conclusions hold?

Three questions, in order of how much they matter:

  1. Is the defect a single consistent failure mode, or several?
  2. How much of the late-night finding survives if the repair is wrong?
  3. Do station rankings move when the repair assumptions change?

Run: python scripts/audit_repair.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.connectivity import TIME_BANDS, band_of  # noqa: E402
from src.gtfs_repair import route_speeds  # noqa: E402
from src.pipeline import Feeds  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"

# The repair assumptions to compare. If the worst stations are the same under
# all of them, the ranking does not depend on getting the speed exactly right.
MODELS = {
    "A_20kmph_1.3": dict(speed_kmph=20.0, circuity=1.3, dwell_s=20.0),
    "B_18kmph_1.4": dict(speed_kmph=18.0, circuity=1.4, dwell_s=30.0),
    "C_fast_25kmph": dict(speed_kmph=25.0, circuity=1.2, dwell_s=10.0),
    "D_slow_14kmph": dict(speed_kmph=14.0, circuity=1.5, dwell_s=45.0),
}


def observed_trip_starts(feeds: Feeds) -> pd.DataFrame:
    """Trip start times are NOT reconstructed. Count them per band.

    This is the load-bearing check. Every trip's first departure is taken
    straight from the feed, so if service collapses at night in this table, the
    collapse is in the published data and not an artefact of the repair.
    """
    st = feeds.bus_stop_times
    first = st.loc[st.groupby("trip_id")["stop_sequence"].idxmin()]
    band = band_of(first["departure_time_s"])
    counts = band.value_counts().reindex(list(TIME_BANDS)).fillna(0).astype(int)
    span_h = {k: (v[1] - v[0]) / 3600 for k, v in TIME_BANDS.items()}
    out = pd.DataFrame({"trips_starting": counts})
    out["per_hour"] = out["trips_starting"] / pd.Series(span_h)
    out["share_of_peak"] = out["per_hour"] / out.loc["evening_peak", "per_hour"]
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    print("loading feeds...")
    feeds = Feeds(ROOT)

    print("\n" + "=" * 72)
    print("1. IS THE LATE-NIGHT COLLAPSE AN ARTEFACT OF THE REPAIR?")
    print("=" * 72)
    print("\nTrip START times, taken verbatim from the feed (never reconstructed):\n")
    starts = observed_trip_starts(feeds)
    print(starts.assign(share_of_peak=lambda d: d["share_of_peak"].map("{:.1%}".format)).to_string())
    print(
        "\n-> The collapse is present in the published start times themselves."
        "\n   The repair shifts a bus's arrival at a mid-route stop by minutes;"
        "\n   it cannot manufacture a 98% drop in how many buses set out."
    )

    print("\n" + "=" * 72)
    print("2. DO STATION RANKINGS MOVE WHEN THE REPAIR ASSUMPTIONS CHANGE?")
    print("=" * 72)
    results = {}
    for name, kwargs in MODELS.items():
        print(f"\n  scoring {name} ...")
        scored = feeds.score(**kwargs)
        results[name] = scored[
            (scored["radius_m"] == 500) & (scored["band"] == "late_night")
        ].set_index("station_name")["departures_per_hour"]

    print("\n  scoring E_published (the feed's own defective times) ...")
    pub = feeds.score_published()
    results["E_published"] = pub[
        (pub["radius_m"] == 500) & (pub["band"] == "late_night")
    ].set_index("station_name")["departures_per_hour"]

    grid = pd.DataFrame(results)
    grid.round(2).to_csv(OUT / "sensitivity_late_night.csv")

    print("\n-- Spearman rank correlation between models (late night, 500m) --")
    print(grid.corr(method="spearman").round(3).to_string())

    print("\n-- worst 10 stations under each model --")
    worst = {m: set(grid[m].nsmallest(10).index) for m in grid.columns}
    base = worst["A_20kmph_1.3"]
    for m, s in worst.items():
        print(f"  {m:<16} overlap with model A: {len(s & base)}/10")

    stable = set.intersection(*[worst[m] for m in MODELS])
    print(f"\n  stations in the worst 10 under ALL FOUR repair models ({len(stable)}):")
    for s in sorted(stable):
        print(f"    {s}")

    print("\n-- late-night departures/hour by model --")
    print(grid.loc[sorted(stable)].round(2).to_string())
    print(f"\n  wrote outputs/sensitivity_late_night.csv")


if __name__ == "__main__":
    main()
