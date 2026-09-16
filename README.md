# Metro–RTC Transfer Gap Atlas

Measuring where a unified Metro + TGSRTC ticket will actually buy you a
connection in Hyderabad, and where it will not.

Built for the **Cyberabad Mobility Data Jam**, 19 September 2026, Cyberabad
Police Commissionerate, Gachibowli.

## The thesis

**A unified ticket does not create a connection.**

In July 2026 the Chief Secretary directed a common ticketing system and a
common mobile application across Hyderabad Metro and TGSRTC. But a single
ticket is worthless to someone who alights at a metro station at 23:00 and
finds no bus within walking distance. Integration is only as good as the worst
transfer in the chain, and nobody has measured Hyderabad's.

This repository measures it, from open GTFS data, for all 57 metro stations
across five time bands and three walk radii.

## Headline finding

Median bus service within 500 m of a metro station:

| Band | Window | Bus departures/hour | Retention |
|---|---|---:|---:|
| Morning peak | 07:00–10:00 | 148.7 | 92% |
| Midday | 10:00–16:00 | 137.2 | 86% |
| Evening peak | 16:00–20:00 | 155.8 | 100% |
| Night | 20:00–23:00 | 97.0 | 64% |
| **Late night** | **23:00–04:00** | **3.2** | **1.9%** |

**Bus service at the average metro station collapses by 98% after 23:00.**

The worst are not peripheral stations:

| Station | Late-night dep/h within 500 m |
|---|---:|
| **Raidurg** | **0.0** |
| Gandhi Hospital | 0.2 |
| Musheerabad | 0.4 |
| RTC Cross Roads | 0.6 |
| Bharat Nagar | 1.0 |

Raidurg is the Blue Line terminus serving the Financial District and
Gachibowli — the IT corridor whose late-shift workers are exactly the people a
unified ticket is meant to attract out of private vehicles. It has **no bus
within 500 m after 23:00 at all**.

This intersects directly with road safety: a significant share of Hyderabad's
fatal crashes occur between 23:00 and 04:00. Where transit stops running,
people drive.

## A data defect you need to know about

**The published TGSRTC departure times are wrong, and wrong by hours.**

In the 08 February 2026 feed, the gap between consecutive `stop_times` grows
linearly with `stop_sequence` instead of tracking distance:

```
corr(gap, stop_sequence) = 0.879
corr(gap, distance)      = 0.061
```

The median distance between consecutive stops stays flat at ~0.72 km for the
whole trip, but the median gap rises from 6 minutes at sequence 1–5 to 121
minutes at sequence 60–90. The implied bus speed therefore decays from
8.1 km/h to 0.44 km/h — slower than walking — purely as a function of how many
stops the bus has already passed. The longest "trip" in the feed is route 300,
Mehdipatnam to JBS, an ordinary city route, encoded as **304 hours**.

Dividing each gap by its sequence index recovers a constant ~20.8 km/h, flat
across the whole trip. The feed generator appears to multiply each increment by
the stop index.

**Why this matters here:** a metro station is almost never the first stop of a
bus trip, so every frequency computed at a transfer point from the published
times is wrong. Any analysis of this feed that does not correct for it is
measuring the bug.

**The correction** (`src/gtfs_repair.py`): trip *start* times are sound — their
distribution peaks at 06:00–08:00 and 14:00–19:00 and tapers to near zero after
23:00, which is what a real bus network looks like. We keep each trip's first
departure and re-derive the rest by propagating along the route at 20 km/h
using straight-line distance scaled by a 1.3 circuity factor, plus 20 s dwell.
Two independent routes to that speed constant agree, and it is consistent with
the TomTom 2025 Hyderabad rush-hour mean of 16.1 km/h.

Reconstructed times are **estimates** and are labelled as such.

## Why the headline metric is retention, not waiting time

The obvious metric — combined expected wait across all nearby routes,
`1 / (2 × Σ(1/hᵢ))` — saturates in Hyderabad and has almost no discriminating
power in daylight. TGSRTC bundles many service variants under one `route_id`
(route 219 alone carries 2,087 trips a day across the
Secunderabad–Patancheruvu–Isnapur corridor), and Secunderabad East sits within
500 m of **341 distinct route_ids**. The formula then returns a few seconds for
nearly every station, which is arithmetically correct and analytically useless:
it says *a* bus will come, not that a *useful* bus will come. Separating useful
from merely present needs origin–destination data the open feeds do not carry.

So `expected_wait_min` is reported as a secondary column, and the headline is
temporal: **what share of a station's own peak service survives into the
night.** That is the quantity the thesis is about, and it discriminates sharply.

## Running it

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
```

```bash
python scripts/validate_feeds.py
```

```bash
python scripts/build_atlas.py
```

### Data

Not committed; both feeds are open and redownloadable.

| Feed | Source | Version |
|---|---|---|
| TGSRTC bus GTFS | [OpenCity](https://data.opencity.in/dataset/hyderabad-bus-stops) | 08 Feb 2026 |
| HMRL metro GTFS | [OpenCity](https://data.opencity.in/dataset/hyderabad-metro-rail-gtfs) | 03 Jul 2026 |

Both published by Open Data Telangana, licensed Other (Public Domain);
attribution required. Place them unzipped in `data/tgsrtc/` and `data/hmrl/`.

**Do not use the undocumented VTPIS real-time ETA endpoints.** The repository
documenting them carries an explicit warning that doing so may violate parts of
the Indian IT Act. If real-time data is needed, ask the organisers — TGSRTC and
the police are co-organisers.

## Feed summary

| | TGSRTC | HMRL |
|---|---|---|
| Stops | 5,028 (4,923 after 50 m dedupe) | 705 platforms → **57 stations** |
| Routes | 1,031 | 3 corridors |
| Trips | 44,892 | 2,820 |
| stop_times | 1,132,807 | 61,236 |
| Service calendar | one always-on id (`MTWTFSS`) | `WK` / `SA` / `SU` |
| Service span | 01:25 – 311:57 (defective) | 06:00 – 23:57 |
| shapes.txt | **absent** | present |
| Coordinates outside Hyderabad | 144 (statewide feed) | 0 |

TGSRTC has **no weekday/weekend distinction at all** — a single service id runs
every day from 2026-02-01 to 2031-02-01. Weekend analysis is not possible from
this feed.

## Outputs

- `outputs/station_connectivity.csv` — the core deliverable: one row per
  (station × radius × band), 855 rows
- `outputs/ranked_stations_500m.csv` — stations ranked by late-night service
- `outputs/stations.geojson` — stations with scores, for mapping

## Stated limitations

These are exclusions, not oversights, and belong in any presentation of the
results.

- **Straight-line buffers, not street-network walks.** A 300 m buffer can be a
  600 m walk. Fixing this needs OSM + `osmnx`.
- **Reconstructed bus times**, for the reason above. Schedule, not reliability.
- **No demand weighting.** All stations are weighted equally; real
  prioritisation needs AFC tap data or ridership by stop.
- **No physical access quality.** Footpath condition, crossing safety and
  shelter presence are absent from GTFS. A 350 m transfer across an eight-lane
  arterial is not a 350 m transfer. This is arguably the most important missing
  variable.
- **MMTS is not included** — not in the provided feeds.
- **Weekends cannot be analysed** for the bus network, per the calendar above.

## What would make this substantially better

The ask, in priority order:

1. AFC tap-in/tap-out logs — actual travel patterns rather than scheduled
2. Bus GPS traces — reliability against schedule, and a ground truth that would
   remove the need for the time reconstruction entirely
3. Ridership by stop — demand weighting
4. Footpath and crossing-quality layers — real walkability
5. Crash locations with timestamps — to test the late-night hypothesis directly

## Layout

```
src/gtfs_loader.py    feed loading, 24:xx-safe time parsing, station dedupe
src/gtfs_repair.py    diagnosis and correction of the TGSRTC time defect
src/spatial.py        EPSG:32644 buffers, 50 m stop clustering, spatial join
src/connectivity.py   time bands, headways, retention, Transfer Gap Score
scripts/validate_feeds.py
scripts/build_atlas.py
```
