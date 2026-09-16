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

### The defect is one failure mode, not several

Fitting `gap = c × stop_sequence` through the origin for every trip with at
least 5 stops (41,975 trips):

| | |
|---|---:|
| Median R² | **0.983** |
| Trips with R² > 0.90 | 94.7% |
| Trips with R² > 0.75 | 98.7% |
| Trips with R² < 0.50 | 0.4% |
| Median `c` | 2.23 min |

Fit quality is uniform across trip lengths. This is a single systematic
generator bug, not a scatter of bad records, which is what makes it correctable
rather than merely disqualifying.

### The late-night finding does not depend on the repair

This is the load-bearing check, and it is why the headline can be presented as
a finding rather than a hypothesis.

**Every trip's first departure is taken verbatim from the feed.** Counting how
many bus trips *set out* in each band uses no reconstruction at all:

| Band | Trips starting | Per hour | Share of peak |
|---|---:|---:|---:|
| Morning peak | 8,004 | 2,668 | 95.9% |
| Midday | 15,585 | 2,598 | 93.3% |
| Evening peak | 11,131 | 2,783 | 100.0% |
| Night | 3,631 | 1,210 | 43.5% |
| **Late night** | **122** | **24.4** | **0.9%** |

The collapse is in the published data. The repair moves a bus's arrival at a
mid-route stop by minutes; it cannot manufacture a 99% drop in how many buses
leave the depot.

### Station rankings are stable across repair assumptions

Four plausible repairs, plus the feed's own defective times as a control:

| Model | Speed | Circuity | Dwell |
|---|---:|---:|---:|
| A (default) | 20 km/h | 1.3 | 20 s |
| B | 18 km/h | 1.4 | 30 s |
| C | 25 km/h | 1.2 | 10 s |
| D | 14 km/h | 1.5 | 45 s |

Spearman rank correlation of late-night service across stations:

| | A | B | C | D | published |
|---|---:|---:|---:|---:|---:|
| **A** | 1.000 | 0.975 | 0.933 | 0.915 | 0.689 |
| **B** | 0.975 | 1.000 | 0.911 | 0.947 | 0.713 |
| **C** | 0.933 | 0.911 | 1.000 | 0.850 | 0.585 |
| **D** | 0.915 | 0.947 | 0.850 | 1.000 | 0.733 |

Six stations sit in the worst ten under **all four** repair models:

| Station | A | B | C | D | published |
|---|---:|---:|---:|---:|---:|
| Raidurg | 0.0 | 0.4 | 0.0 | 0.8 | 12.4 |
| Gandhi Hospital | 0.2 | 0.6 | 0.4 | 2.2 | 56.4 |
| Musheerabad | 0.4 | 0.4 | 0.2 | 1.8 | 59.2 |
| RTC Cross Roads | 0.6 | 1.2 | 0.4 | 2.0 | 62.2 |
| Bharat Nagar | 1.0 | 2.0 | 0.4 | 3.8 | 69.2 |
| Erragadda | 1.4 | 2.0 | 0.6 | 3.8 | 81.6 |

Note the last column. Using the feed's published times, every one of these
stations looks adequately served at night. **Without the repair you do not get
a weaker version of this finding — you get the opposite of it.**

Reproduce with `python scripts/audit_repair.py`.

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

### Destination reach: a first cut at "useful", without demand data

Counting routes does not answer whether a transfer opens the city up. But 79%
of TGSRTC `trip_short_name` values encode their terminals
("219-SILVER ROUTE-SECUNDERABAD-TO-PATANCHERUVU"), so the *destination* of each
trip can be parsed straight out of the feed. Counting distinct destinations
reachable from a station measures reach rather than volume.

Median distinct bus destinations within 500 m of a metro station:

| Band | Destinations |
|---|---:|
| Morning peak | 54 |
| Midday | 60 |
| Evening peak | 51 |
| Night | 32 |
| **Late night** | **6** |

The saturated interchanges do not escape it. Secunderabad East — the station
with 341 route_ids — falls from **208 destinations to 13** after 23:00, a 94%
loss of reach. Volume and reach collapse together.

The sharpest pattern is geographic. Along the Blue Line through the IT
corridor, late-night destination reach is:

| Station | Evening peak | Late night |
|---|---:|---:|
| Raidurg | 26 | **0** |
| Road No 5 Jubilee Hills | 9 | **0** |
| Yusufguda | 18 | **0** |
| Durgam Cheruvu | 17 | 1 |
| HITEC City | 37 | 1 |

This is not a scatter of unlucky stations. **The entire western IT corridor
loses its bus network after 23:00** — the corridor whose late-shift workers are
precisely the people a unified ticket is meant to move out of private vehicles,
in the window when Hyderabad's fatal crashes concentrate.

This is a proxy. It measures reach, not whether anyone wants to go there;
that still needs AFC or ridership data.

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

```bash
python scripts/audit_repair.py
```

```bash
python scripts/optimise_late_night.py
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
- `outputs/optimisation_budget_sweep.csv` — GA vs greedy across budgets
- `outputs/late_night_service_plan.csv` — the recommended restart plan
- `outputs/transfer_gap_atlas.html` — the interactive map: a time slider that
  swaps each station's metrics in place, so dragging evening → night →
  late night shows the bus network around the metro going out, with a live KPI
  panel. Self-contained; open it in any browser.

The map uses key-free OpenStreetMap tiles darkened client-side rather than a
dark CartoDB style, which now needs an API key. If tiles fail to load — the
venue is expected to have poor wifi — the dark background, metro corridors and
station markers still render, so the demo degrades instead of dying.

## What it would take to fix: late-night service allocation

The Atlas measures the gap. `scripts/optimise_late_night.py` asks the
operator's question: **given a fixed budget of extra bus-hours after 23:00,
which services should run?**

Every candidate is a route that *already* serves one of the six robustly-worst
stations at evening peak and has stopped by the late-night window. The decision
is which dormant services to restart, not which routes to invent — so the
answer is an operating-hours change on existing alignments, with vehicles,
drivers and routes TGSRTC already has.

**The decision variable is the route, not the station.** 52 of the 114
candidate routes pass two or more of the six target stations. Restarting one is
a single decision at a single cost benefiting every target station on its
alignment; pricing it per station charges the operator two or three times for
one bus. Correcting this roughly doubled measured value at a fixed budget
(30 → 52 connections for the same 30 hours).

### Result at 30 additional bus-hours

Six routes restarted, 29.7 bus-hours, **52 station–destination connections
created** — 1.8 per bus-hour.

| Route | Bus-hours | Target stations served | Destinations added |
|---|---:|---:|---:|
| 1/25S | 8.0 | 3 | 15 |
| 107VR | 3.9 | 3 | 13 |
| 218C | 9.3 | 2 | 11 |
| 1Z | 4.9 | 3 | 6 |
| 186 | 2.4 | 2 | 5 |
| 10Y/F | 1.1 | 2 | 3 |

| Station | Reachable before | Gained |
|---|---:|---:|
| Gandhi Hospital | 0 | +12 |
| Musheerabad | 1 | +11 |
| RTC Cross Roads | 2 | +11 |
| Erragadda | 3 | +10 |
| Bharat Nagar | 3 | +8 |
| **Raidurg** | **0** | **0** |

### The equity finding

Told only to maximise connections, **the optimiser abandons Raidurg** — the one
station with no late-night bus service at all. Its routes are long and serve no
other target station, so every bus-hour spent there buys fewer connections than
elsewhere. Efficiency and need point in opposite directions.

Forcing it back in, at the same budget: restart route 195W for 5.9 bus-hours,
Raidurg goes 0 → 7 destinations, and total connections fall 52 → 51.

**Reconnecting Raidurg costs about 2% of the network-wide gain.**

That is the number worth putting in front of a planner. It is reported rather
than folded into the fitness as an equity weight, because a weight would decide
the trade-off invisibly and unarguably; a planner should make this call.

### An honest note on the genetic algorithm

Against a greedy best-ratio baseline the GA finds **0% to 4.3% more**
connections depending on budget, converging within about 10 generations:

| Budget (bus-h) | Greedy | GA | Lift |
|---:|---:|---:|---:|
| 10 | 23 | 24 | +4.3% |
| 20 | 39 | 40 | +2.6% |
| 30 | 50 | 52 | +4.0% |
| 50 | 72 | 73 | +1.4% |
| 100 | 108 | 108 | 0.0% |

Budgeted maximum coverage has a well-known (1 − 1/e) greedy guarantee, and this
instance sits close to it. Claiming the GA was indispensable when a short greedy
gets within a few percent would not survive questioning, so the baseline ships
in the code and the comparison runs every time.

The GA earns its place by being what *proves* greedy is near-optimal here, and
by generalising to constraints this model does not yet carry — vehicle chaining
between routes, depot availability, crew hours — where the greedy ratio argument
breaks down.

## Stated limitations

These are exclusions, not oversights, and belong in any presentation of the
results.

- **Straight-line buffers, not street-network walks.** A 300 m buffer can be a
  600 m walk. Fixing this needs OSM + `osmnx`.
- **Reconstructed bus times**, for the reason above — though the headline
  survives all four repair models tested, and rests on observed trip starts.
  Schedule, not reliability, either way.
- **Destination reach is a proxy for usefulness**, parsed from trip names. It
  measures where buses go, not where people want to go.
- **No demand weighting.** All stations are weighted equally; real
  prioritisation needs AFC tap data or ridership by stop.
- **No physical access quality.** Footpath condition, crossing safety and
  shelter presence are absent from GTFS. A 350 m transfer across an eight-lane
  arterial is not a 350 m transfer. This is arguably the most important missing
  variable.
- **MMTS is not included** — not in the provided feeds.
- **The optimiser ignores vehicle chaining, depots and crew hours.** It costs a
  restarted route as round-trip vehicle-hours and assumes a bus is available.
  Real scheduling would couple the routes together.
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
src/destinations.py   terminal parsing, destination-reach metric
src/pipeline.py       reusable end-to-end run, so assumptions can be varied
src/mapping.py        interactive Leaflet map: slider, KPI panel, metro lines
src/optimise.py       late-night bus-hour allocation: GA + greedy baseline
scripts/validate_feeds.py
scripts/build_atlas.py
scripts/audit_repair.py   defect audit + repair sensitivity analysis
scripts/optimise_late_night.py   where to deploy additional bus-hours
```
