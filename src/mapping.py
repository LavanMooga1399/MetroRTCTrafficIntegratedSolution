"""The Transfer Gap Atlas: an interactive decision-support map.

Two modes over one map.

  Network   — drag through the day and watch bus service around the metro go
              out. The slider swaps each station's metrics in place rather than
              toggling pre-rendered layers, so markers resize and dim as you
              move and the eye tracks the change.

  Where to add — the optimiser's answer. Pick a budget of extra bus-hours after
              23:00 and the routes it would restart are drawn on the map, with
              the equity trade-off as a toggle rather than a hidden weight.

DESIGN NOTES
------------
The vernacular here is a departure board, not a dashboard. The hero is the time
window set large, because the whole finding is what time it is. Service is
encoded primarily by LUMINANCE -- gold when running, dimming through ember, a
hollow ring when gone -- so "the network goes dark" is literal and legible
before anyone reads the legend, and stays ordered for colour-blind viewers.
Saturated hue is reserved for the three metro corridors, using the official
colours that ship in the HMRL feed, so nothing competes with them.

Metric labels are written for a planner reading them cold: "Buses an hour",
not "median departures_per_hour".
"""

from __future__ import annotations

import json
from pathlib import Path

import branca
import folium
import pandas as pd

from .connectivity import TIME_BANDS

BAND_CLOCK = {
    "morning_peak": "07:00 – 10:00",
    "midday": "10:00 – 16:00",
    "evening_peak": "16:00 – 20:00",
    "night": "20:00 – 23:00",
    "late_night": "23:00 – 04:00",
}
BAND_NAME = {
    "morning_peak": "Morning rush",
    "midday": "Middle of the day",
    "evening_peak": "Evening rush",
    "night": "Evening",
    "late_night": "Late night",
}
BAND_TICK = {
    "morning_peak": "7am",
    "midday": "10am",
    "evening_peak": "4pm",
    "night": "8pm",
    "late_night": "11pm",
}

# Luminance-ordered: brightest is full service, and "none" is a hollow ring so
# it reads as absence rather than as another colour on the scale.
GRADE_COLOURS = {
    "maintained": "#FFD166",
    "reduced": "#F2A65A",
    "poor": "#DD7F5C",
    "collapsed": "#B9566E",
    "no_service": "#FF4D6D",
}
GRADE_WORDS = {
    "maintained": "Running normally",
    "reduced": "Thinned out",
    "poor": "Barely running",
    "collapsed": "Nearly gone",
    "no_service": "No bus at all",
}

HYDERABAD_CENTRE = (17.42, 78.47)


def _metro_lines(metro_feed: dict[str, pd.DataFrame]) -> list[dict]:
    shapes = metro_feed["shapes"].copy()
    shapes["shape_pt_sequence"] = pd.to_numeric(shapes["shape_pt_sequence"])
    for col in ("shape_pt_lat", "shape_pt_lon"):
        shapes[col] = pd.to_numeric(shapes[col])

    shape_route = (
        metro_feed["trips"][["route_id", "shape_id"]]
        .drop_duplicates()
        .set_index("shape_id")["route_id"]
    )
    colours = metro_feed["routes"].set_index("route_id")["route_color"]

    lines = []
    for shape_id, group in shapes.groupby("shape_id"):
        route_id = shape_route.get(shape_id)
        group = group.sort_values("shape_pt_sequence")
        lines.append(
            {
                "route_id": route_id,
                "colour": f"#{colours.get(route_id, '888888')}",
                "points": group[["shape_pt_lat", "shape_pt_lon"]].values.tolist(),
            }
        )
    return lines


def _station_payload(scored: pd.DataFrame, stations: pd.DataFrame, radius_m: int) -> dict:
    at_radius = scored[scored["radius_m"] == radius_m]
    coords = stations.set_index("stop_id")[["stop_lat", "stop_lon"]]

    by_station: dict[str, dict] = {}
    for station_id, group in at_radius.groupby("station_id"):
        if station_id not in coords.index:
            continue
        bands = {}
        for _, row in group.iterrows():
            bands[row["band"]] = {
                "dep": round(float(row["departures_per_hour"]), 1),
                "dest": int(row["n_destinations"]),
                "routes": int(row["n_routes"]),
                "ret": round(float(row["retention"]), 4),
                "walk": (
                    round(float(row["nearest_bus_stop_m"]))
                    if pd.notna(row["nearest_bus_stop_m"])
                    else None
                ),
                "grade": str(row["grade"]),
            }
        by_station[station_id] = {
            "name": group["station_name"].iloc[0],
            "lat": float(coords.loc[station_id, "stop_lat"]),
            "lon": float(coords.loc[station_id, "stop_lon"]),
            "bands": bands,
        }

    kpis = {}
    for band in TIME_BANDS:
        b = at_radius[at_radius["band"] == band]
        if b.empty:
            continue
        kpis[band] = {
            "served": int((b["departures_per_hour"] > 0).sum()),
            "total": int(len(b)),
            "dep": round(float(b["departures_per_hour"].median()), 1),
            "dest": int(b["n_destinations"].median()),
            "ret": round(float(b["retention"].median()), 4),
        }
    return {"stations": by_station, "kpis": kpis}


def _css() -> str:
    return """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --ground: #080B14;
    --surface: #101726;
    --surface-2: #172033;
    --edge: rgba(150,170,210,0.14);
    --ink: #EAEEF7;
    --ink-dim: #99A5BF;
    --ink-faint: #64708C;
    --gold: #FFD166;
    --alarm: #FF4D6D;
    --sans: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
    --mono: "IBM Plex Mono", ui-monospace, "SF Mono", monospace;
  }
  html, body, #map, .folium-map { background: var(--ground) !important; }
  .leaflet-tile-pane {
    filter: invert(1) hue-rotate(190deg) brightness(0.82) contrast(0.92) saturate(0.4);
  }
  .leaflet-control-attribution {
    background: rgba(8,11,20,0.82) !important; color: var(--ink-faint) !important;
    font-family: var(--sans) !important; font-size: 10px !important;
  }
  .leaflet-control-attribution a { color: var(--ink-faint) !important; }
  .leaflet-control-scale-line {
    background: rgba(8,11,20,0.8); color: var(--ink-dim);
    border-color: var(--edge) !important; font-family: var(--mono);
  }

  /* ---------- left rail ---------- */
  .atlas {
    position: absolute; top: 0; left: 0; bottom: 0; width: 352px; z-index: 9999;
    background: linear-gradient(180deg, var(--surface) 0%, #0C1220 100%);
    border-right: 1px solid var(--edge);
    font-family: var(--sans); color: var(--ink);
    display: flex; flex-direction: column;
    overflow-y: auto; overscroll-behavior: contain;
  }
  .atlas::-webkit-scrollbar { width: 8px; }
  .atlas::-webkit-scrollbar-thumb { background: var(--surface-2); border-radius: 4px; }
  .atlas__head { padding: 22px 24px 18px; border-bottom: 1px solid var(--edge); }
  .atlas__name {
    margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.02em;
  }
  .atlas__sub { margin: 4px 0 0; font-size: 13px; color: var(--ink-dim); }

  .modes { display: flex; gap: 2px; margin: 16px 0 0;
    background: rgba(0,0,0,0.3); padding: 3px; border-radius: 8px; }
  .modes button {
    flex: 1; appearance: none; border: 0; border-radius: 6px; cursor: pointer;
    padding: 9px 10px; font: 500 13px var(--sans); color: var(--ink-dim);
    background: transparent; transition: background .16s, color .16s;
  }
  .modes button[aria-selected="true"] { background: var(--surface-2); color: var(--ink); }
  .modes button:focus-visible { outline: 2px solid var(--gold); outline-offset: 1px; }

  .panel { padding: 20px 24px 24px; }
  .panel[hidden] { display: none !important; }

  /* the hero: the time window itself */
  .clock { font: 600 34px/1 var(--mono); letter-spacing: -0.03em; margin: 0; }
  .clock__name { margin: 6px 0 0; font-size: 14px; color: var(--ink-dim); }

  .slider { width: 100%; margin: 20px 0 6px; accent-color: var(--gold); }
  .slider:focus-visible { outline: 2px solid var(--gold); outline-offset: 4px; }
  .ticks { display: flex; justify-content: space-between;
    font: 400 11px var(--mono); color: var(--ink-faint); }

  /* Readings laid out as a timetable grid rather than cards: hairlines carry
     the structure, and an inline bar encodes each value against the day's
     peak so the collapse is visible before any number is read. */
  .kpis { display: grid; grid-template-columns: 1fr 1fr; margin: 20px 0 0;
    border-top: 1px solid var(--edge); }
  .kpi { padding: 15px 14px 14px 0; border-bottom: 1px solid var(--edge); }
  .kpi:nth-child(even) { padding-left: 16px; border-left: 1px solid var(--edge); }
  .kpi__n { display: block; font: 600 27px/1.05 var(--mono);
    letter-spacing: -0.03em; transition: color .2s; }
  .kpi__unit { display: block; margin-top: 5px; font-size: 12.5px; font-weight: 500; }
  .kpi__qual { display: block; margin-top: 2px; font-size: 11.5px; line-height: 1.35;
    color: var(--ink-faint); }
  .kpi__track { height: 3px; margin-top: 10px; background: rgba(255,255,255,0.07);
    border-radius: 2px; }
  .kpi__fill { height: 100%; width: 0; background: var(--gold); border-radius: 2px;
    transition: width .28s ease; }
  .is-alarm .kpi__n { color: var(--alarm); }
  .is-alarm .kpi__fill { background: var(--alarm); }

  /* both solutions, side by side, so the trade-off is read rather than toggled */
  .compare { margin: 18px 0 0; border-top: 1px solid var(--edge); }
  .compare__row { display: grid; grid-template-columns: 1fr auto auto; gap: 12px;
    align-items: baseline; padding: 11px 8px 11px 0;
    border-bottom: 1px solid var(--edge); transition: opacity .18s; }
  .compare__row[data-active="false"] { opacity: 0.42; }
  .compare__name { font-size: 13px; font-weight: 500; }
  .compare__sub { display: block; margin-top: 2px; font-size: 11.5px; color: var(--ink-faint); }
  .compare__n { font: 600 17px var(--mono); }
  .compare__tag { font: 500 11.5px var(--mono); color: var(--ink-faint); min-width: 74px;
    text-align: right; }
  .compare__row[data-kind="equity"] .compare__n { color: var(--alarm); }

  /* optimise mode */
  .lede { margin: 0 0 18px; font-size: 13.5px; line-height: 1.5; color: var(--ink-dim); }
  .result { display: flex; align-items: baseline; gap: 10px; margin: 4px 0 2px; }
  .result__n { font: 600 40px/1 var(--mono); letter-spacing: -0.03em; color: var(--gold); }
  .result__unit { font-size: 14px; color: var(--ink-dim); }
  .result__note { margin: 6px 0 0; font-size: 12.5px; color: var(--ink-faint); }

  .equity { margin: 18px 0 0; padding: 14px 16px; border: 1px solid var(--edge);
    border-radius: 10px; background: rgba(255,77,109,0.05); }
  .equity__row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .equity__label { font-size: 13.5px; font-weight: 500; }
  .equity__note { margin: 7px 0 0; font-size: 12.5px; line-height: 1.5; color: var(--ink-dim); }
  .switch { position: relative; width: 40px; height: 22px; flex: none; }
  .switch input { position: absolute; inset: 0; opacity: 0; cursor: pointer; margin: 0; }
  .switch span { position: absolute; inset: 0; border-radius: 11px; cursor: pointer;
    background: rgba(255,255,255,0.14); transition: background .18s; }
  .switch span::after { content: ""; position: absolute; top: 3px; left: 3px;
    width: 16px; height: 16px; border-radius: 50%; background: var(--ink);
    transition: transform .18s; }
  .switch input:checked + span { background: var(--alarm); }
  .switch input:checked + span::after { transform: translateX(18px); }
  .switch input:focus-visible + span { outline: 2px solid var(--gold); outline-offset: 2px; }

  .routes { margin: 20px 0 0; }
  .routes__head { display: flex; justify-content: space-between; align-items: baseline;
    padding-bottom: 8px; border-bottom: 1px solid var(--edge); }
  .routes__title { font-size: 14px; font-weight: 600; }
  .routes__count { font: 400 12px var(--mono); color: var(--ink-faint); }
  .route { display: grid; grid-template-columns: auto 1fr auto; gap: 10px;
    align-items: baseline; width: 100%; text-align: left;
    padding: 11px 8px 11px 10px; margin: 2px 0 0;
    background: transparent; border: 0; border-radius: 7px; cursor: pointer;
    color: inherit; font-family: var(--sans); transition: background .14s; }
  .route:hover, .route.is-on { background: var(--surface-2); }
  .route:focus-visible { outline: 2px solid var(--gold); outline-offset: -2px; }
  .route__id { font: 600 13px var(--mono); color: var(--gold); }
  .route__where { font-size: 12.5px; color: var(--ink-dim);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .route__n { font: 500 12.5px var(--mono); color: var(--ink-dim); }
  .route.is-forced .route__id { color: var(--alarm); }

  /* legend, bottom right of the map */
  .key { position: absolute; right: 16px; bottom: 30px; z-index: 9999;
    background: rgba(16,23,38,0.93); border: 1px solid var(--edge);
    border-radius: 10px; padding: 13px 15px; font-family: var(--sans);
    color: var(--ink); width: 196px; }
  .key h2 { margin: 0 0 9px; font-size: 12.5px; font-weight: 600; color: var(--ink-dim); }
  /* scoped to the rows, not the container -- `.key div` would also match it */
  .key > div > div { display: flex; align-items: center; gap: 9px;
    padding: 3px 0; font-size: 12.5px; }
  .key i { width: 11px; height: 11px; border-radius: 50%; flex: none; box-sizing: border-box; }

  /* popups */
  .leaflet-popup-content-wrapper {
    background: var(--surface); color: var(--ink);
    border: 1px solid var(--edge); border-radius: 10px;
    box-shadow: 0 18px 50px rgba(0,0,0,0.6);
  }
  .leaflet-popup-content { margin: 14px 16px; font-family: var(--sans); }
  .leaflet-popup-tip { background: var(--surface); border: 1px solid var(--edge); }
  .leaflet-popup-close-button { color: var(--ink-faint) !important; }
  .pop h3 { margin: 0 0 3px; font-size: 16px; font-weight: 600; }
  .pop__state { margin: 0 0 10px; font-size: 12.5px; }
  .pop table { border-collapse: collapse; }
  .pop td { padding: 3px 0; font-size: 13px; }
  .pop td:first-child { padding-right: 18px; color: var(--ink-dim); }
  .pop td:last-child { font: 500 13px var(--mono); text-align: right; }

  @media (prefers-reduced-motion: reduce) {
    * { transition: none !important; animation: none !important; }
  }
  @media (max-width: 760px) {
    .atlas { width: 100%; bottom: auto; max-height: 52%; border-right: 0;
      border-bottom: 1px solid var(--edge); }
    .key { display: none; }
  }
</style>
"""


def _html(band_order: list[str], has_optimisation: bool) -> str:
    ticks = "".join(f"<span>{BAND_TICK[b]}</span>" for b in band_order)
    optimise_tab = (
        '<button id="tab-add" role="tab" aria-selected="false">Intervention</button>'
        if has_optimisation
        else ""
    )
    optimise_panel = (
        """
  <section class="panel" id="panel-add" role="tabpanel" hidden>
    <p class="lede">Where could additional service help? Bus service near the
      metro all but stops after 11pm. These are the existing routes worth
      keeping on the road in that window.</p>

    <p class="clock" id="budget-label">30 bus-hours</p>
    <p class="clock__name">added between 11pm and 4am</p>
    <input class="slider" id="budget" type="range" min="0" max="4" step="1" value="2"
           aria-label="Extra bus-hours available after 11pm">
    <div class="ticks" id="budget-ticks"></div>

    <div class="result">
      <span class="result__n" id="opt-connections">—</span>
      <span class="result__unit">new station–destination connections</span>
    </div>
    <p class="result__note" id="opt-note">—</p>

    <div class="equity" id="equity" hidden>
      <div class="equity__row">
        <span class="equity__label">Minimum service guarantee</span>
        <label class="switch">
          <input type="checkbox" id="equity-toggle" aria-describedby="equity-note">
          <span></span>
        </label>
      </div>
      <p class="equity__note" id="equity-note">—</p>
      <div class="compare" id="compare"></div>
    </div>

    <div class="routes">
      <div class="routes__head">
        <span class="routes__title">Routes to restart</span>
        <span class="routes__count" id="routes-count">—</span>
      </div>
      <div id="routes-list"></div>
    </div>
  </section>"""
        if has_optimisation
        else ""
    )

    return f"""
<div class="atlas">
  <header class="atlas__head">
    <h1 class="atlas__name">Transfer Gap Atlas</h1>
    <p class="atlas__sub">Does a metro ticket get you home? Hyderabad, all 57 stations.</p>
    <div class="modes" role="tablist">
      <button id="tab-network" role="tab" aria-selected="true">Network</button>
      {optimise_tab}
    </div>
  </header>

  <section class="panel" id="panel-network" role="tabpanel">
    <p class="lede">Where does bus–metro connectivity break down?</p>
    <p class="clock" id="clock">—</p>
    <p class="clock__name" id="clock-name">—</p>
    <input class="slider" id="band" type="range" min="0" max="{len(band_order) - 1}"
           step="1" value="2" aria-label="Time of day">
    <div class="ticks">{ticks}</div>

    <div class="kpis">
      <div class="kpi" id="m-served">
        <span class="kpi__n" id="v-served">—</span>
        <span class="kpi__unit">metro stations</span>
        <span class="kpi__qual">still connected — at least one bus within 500 m</span>
        <div class="kpi__track"><div class="kpi__fill" id="f-served"></div></div>
      </div>
      <div class="kpi" id="m-dep">
        <span class="kpi__n" id="v-dep">—</span>
        <span class="kpi__unit">buses per hour</span>
        <span class="kpi__qual">median across metro stations, within 500 m</span>
        <div class="kpi__track"><div class="kpi__fill" id="f-dep"></div></div>
      </div>
      <div class="kpi" id="m-dest">
        <span class="kpi__n" id="v-dest">—</span>
        <span class="kpi__unit">destinations</span>
        <span class="kpi__qual">median reachable by bus from a metro station</span>
        <div class="kpi__track"><div class="kpi__fill" id="f-dest"></div></div>
      </div>
      <div class="kpi" id="m-ret">
        <span class="kpi__n" id="v-ret">—</span>
        <span class="kpi__unit">evening service left</span>
        <span class="kpi__qual">median share of the same station's 4–8pm service</span>
        <div class="kpi__track"><div class="kpi__fill" id="f-ret"></div></div>
      </div>
    </div>
  </section>
{optimise_panel}
</div>

<aside class="key">
  <h2 id="key-title">Bus service near each station</h2>
  <div id="key-body"></div>
</aside>
"""


def _script(payload: dict, band_order: list[str], optimisation: dict | None) -> str:
    return f"""
(function () {{
  var DATA = {json.dumps(payload)};
  var BANDS = {json.dumps(band_order)};
  var CLOCK = {json.dumps(BAND_CLOCK)};
  var NAMES = {json.dumps(BAND_NAME)};
  var COLOURS = {json.dumps(GRADE_COLOURS)};
  var WORDS = {json.dumps(GRADE_WORDS)};
  var OPT = {json.dumps(optimisation)};

  var map = null, stationLayer = null, routeLayer = null;
  var mode = 'network', activeRoute = null;

  function findMap() {{
    var keys = Object.keys(window);
    for (var i = 0; i < keys.length; i++) {{
      var k = keys[i];
      if (k.indexOf('map_') === 0 && window[k] && window[k]._container) return window[k];
    }}
    return null;
  }}

  var $ = function (id) {{ return document.getElementById(id); }};
  function peak(metric) {{
    var k = DATA.kpis['evening_peak'] || {{}};
    return k[metric] || 1;
  }}

  // ---------- network mode ----------

  function radius(dep) {{
    return dep <= 0 ? 4.5 : Math.max(4.5, Math.min(15, 1.6 * Math.sqrt(dep)));
  }}

  function stationPopup(name, m) {{
    return '<div class="pop"><h3>' + name + '</h3>' +
      '<p class="pop__state" style="color:' + (COLOURS[m.grade] || '#FF4D6D') + '">' +
        (WORDS[m.grade] || '') + '</p><table>' +
      '<tr><td>Buses an hour</td><td>' + m.dep + '</td></tr>' +
      '<tr><td>Places you can get to</td><td>' + m.dest + '</td></tr>' +
      '<tr><td>Bus routes nearby</td><td>' + m.routes + '</td></tr>' +
      '<tr><td>Share of 4–8pm service</td><td>' + (m.ret * 100).toFixed(1) + '%</td></tr>' +
      '<tr><td>Walk to nearest stop</td><td>' +
        (m.walk === null ? 'no stop within 500 m' : m.walk + ' m') + '</td></tr>' +
      '</table></div>';
  }}

  function drawNetwork(i) {{
    var band = BANDS[i];
    stationLayer.clearLayers();
    routeLayer.clearLayers();

    Object.keys(DATA.stations).forEach(function (id) {{
      var s = DATA.stations[id];
      var m = s.bands[band] ||
        {{dep: 0, dest: 0, routes: 0, ret: 0, walk: null, grade: 'no_service'}};
      var colour = COLOURS[m.grade] || '#FF4D6D';
      var gone = m.dep <= 0;

      L.circleMarker([s.lat, s.lon], {{
        radius: radius(m.dep),
        color: gone ? '#FF4D6D' : colour,
        weight: gone ? 2 : 1,
        opacity: 0.9,
        fillColor: colour,
        fillOpacity: gone ? 0 : 0.55
      }}).bindPopup(stationPopup(s.name, m))
        .bindTooltip(s.name, {{direction: 'top'}})
        .addTo(stationLayer);

      if (gone) {{
        L.circleMarker([s.lat, s.lon], {{
          radius: 14, color: '#FF4D6D', weight: 1.2, opacity: 0.7, fill: false
        }}).addTo(stationLayer);
      }}
    }});

    var k = DATA.kpis[band] || {{}};
    $('clock').textContent = CLOCK[band];
    $('clock-name').textContent = NAMES[band];
    setKpi('served', k.served != null ? k.served + ' / ' + k.total : '—',
           k.served, k.total || 57);
    setKpi('dep', k.dep != null ? String(k.dep) : '—', k.dep, peak('dep'));
    setKpi('dest', k.dest != null ? String(k.dest) : '—', k.dest, peak('dest'));
    setKpi('ret', k.ret != null ? (k.ret * 100).toFixed(1) + '%' : '—', k.ret, 1);
    var alarm = k.ret != null && k.ret < 0.15;
    ['dep', 'dest', 'ret', 'served'].forEach(function (id) {{
      $('m-' + id).classList.toggle('is-alarm', alarm);
    }});
  }}

  function setKpi(id, text, value, max) {{
    $('v-' + id).textContent = text;
    var pct = (value == null || !max) ? 0 : Math.max(0, Math.min(100, 100 * value / max));
    $('f-' + id).style.width = pct + '%';
  }}

  // ---------- optimise mode ----------

  function currentSolution() {{
    var budget = String(OPT.budgets[+$('budget').value]);
    var sol = OPT.solutions[budget];
    var useEquity = $('equity-toggle').checked && sol.equity;
    return {{
      budget: budget,
      base: sol,
      routes: useEquity ? sol.equity.routes : sol.routes,
      connections: useEquity ? sol.equity.connections : sol.connections,
      equityOn: useEquity
    }};
  }}

  function drawOptimised() {{
    var s = currentSolution();
    stationLayer.clearLayers();
    routeLayer.clearLayers();

    $('budget-label').textContent = s.budget + ' bus-hours';
    $('opt-connections').textContent = s.connections;
    $('opt-note').textContent =
      s.routes.length + ' routes restarted · ' +
      (s.connections / Number(s.budget)).toFixed(1) + ' connections for each bus-hour · ' +
      'a greedy baseline finds ' + s.base.greedy;

    var eq = s.base.equity;
    $('equity').hidden = !eq;
    if (eq) {{
      $('equity-note').textContent = eq.station + ' has no late-night bus at all, but its ' +
        'routes are long and shared with no other gap station, so maximising connections ' +
        'skips it. Requiring service there restarts route ' + eq.forced_route + ' for ' +
        eq.forced_hours.toFixed(1) + ' bus-hours.';
      // Both scenarios stay on screen. The trade-off is something a planner
      // should read side by side, not discover by flipping a switch.
      $('compare').innerHTML =
        compareRow('efficiency', 'Efficiency only', 'maximise connections',
                   s.base.connections, eq.station + ': 0', !s.equityOn) +
        compareRow('equity', 'Minimum service', eq.station + ' must be served',
                   eq.connections, eq.station + ': ' + eq.station_gain, s.equityOn);
    }} else {{
      $('compare').innerHTML = '';
    }}

    // Target stations: filled where the plan reaches them, hollow where not.
    var gains = s.equityOn && eq ? eq.per_station : s.base.per_station;
    Object.keys(OPT.targets).forEach(function (name) {{
      var t = OPT.targets[name];
      var g = gains[name] || 0;
      L.circleMarker([t.lat, t.lon], {{
        radius: g > 0 ? Math.max(7, Math.min(16, 4 + g)) : 8,
        color: g > 0 ? '#FFD166' : '#FF4D6D',
        weight: 2, opacity: 0.95,
        fillColor: '#FFD166', fillOpacity: g > 0 ? 0.35 : 0
      }}).bindTooltip(
          name + (g > 0 ? ' — ' + g + ' places added' : ' — still nothing'),
          {{direction: 'top'}}
        ).addTo(stationLayer);
    }});

    s.routes.forEach(function (r) {{
      var pts = OPT.paths[r.route_id];
      if (!pts || pts.length < 2) return;
      L.polyline(pts, {{
        color: r.forced ? '#FF4D6D' : '#FFD166',
        weight: activeRoute === r.route_id ? 4.5 : 2.2,
        opacity: activeRoute && activeRoute !== r.route_id ? 0.28 : 0.85
      }}).bindTooltip(
          'Route ' + r.route_id + ' — ' + r.destinations_added + ' places added',
          {{sticky: true}}
        ).addTo(routeLayer);
    }});

    renderRouteList(s);
    $('key-title').textContent = 'The plan';
    $('key-body').innerHTML =
      '<div><i style="background:#FFD166"></i>Route to restart</div>' +
      '<div><i style="background:#FFD166;opacity:.45"></i>Station reconnected</div>' +
      '<div><i style="border:2px solid #FF4D6D"></i>Still no service</div>';
  }}

  function compareRow(kind, name, sub, n, tag, active) {{
    return '<div class="compare__row" data-kind="' + kind + '" data-active="' + active + '">' +
      '<span class="compare__name">' + name +
        '<span class="compare__sub">' + sub + '</span></span>' +
      '<span class="compare__n">' + n + '</span>' +
      '<span class="compare__tag">' + tag + '</span></div>';
  }}

  function renderRouteList(s) {{
    $('routes-count').textContent =
      s.routes.reduce(function (a, r) {{ return a + r.hours; }}, 0).toFixed(1) + ' bus-hours';
    var list = $('routes-list');
    list.innerHTML = '';
    s.routes.forEach(function (r) {{
      var b = document.createElement('button');
      b.className = 'route' + (r.forced ? ' is-forced' : '') +
        (activeRoute === r.route_id ? ' is-on' : '');
      b.innerHTML =
        '<span class="route__id">' + r.route_id + '</span>' +
        '<span class="route__where">' + r.stations.join(', ') + '</span>' +
        '<span class="route__n">+' + r.destinations_added + '</span>';
      b.title = r.hours.toFixed(1) + ' bus-hours · serves ' + r.stations.length +
        ' gap stations · adds ' + r.destinations_added + ' places';
      b.addEventListener('click', function () {{
        activeRoute = activeRoute === r.route_id ? null : r.route_id;
        drawOptimised();
        var pts = OPT.paths[r.route_id];
        if (activeRoute && pts && pts.length > 1) map.fitBounds(L.latLngBounds(pts), {{padding: [60, 60]}});
      }});
      list.appendChild(b);
    }});
  }}

  // ---------- chrome ----------

  function setMode(next) {{
    mode = next;
    var isNet = mode === 'network';
    $('tab-network').setAttribute('aria-selected', String(isNet));
    $('panel-network').hidden = !isNet;
    if ($('tab-add')) {{
      $('tab-add').setAttribute('aria-selected', String(!isNet));
      $('panel-add').hidden = isNet;
    }}
    if (isNet) {{
      $('key-title').textContent = 'Bus service near each station';
      $('key-body').innerHTML = Object.keys(WORDS).map(function (g) {{
        var style = g === 'no_service'
          ? 'border:2px solid ' + COLOURS[g]
          : 'background:' + COLOURS[g];
        return '<div><i style="' + style + '"></i>' + WORDS[g] + '</div>';
      }}).join('');
      drawNetwork(+$('band').value);
    }} else {{
      activeRoute = null;
      drawOptimised();
    }}
  }}

  function start() {{
    map = findMap();
    if (!map) return window.setTimeout(start, 60);
    stationLayer = L.layerGroup().addTo(map);
    routeLayer = L.layerGroup().addTo(map);
    L.control.scale({{imperial: false, position: 'bottomleft'}}).addTo(map);

    $('band').addEventListener('input', function () {{ drawNetwork(+this.value); }});
    $('tab-network').addEventListener('click', function () {{ setMode('network'); }});

    if (OPT) {{
      $('budget-ticks').innerHTML =
        OPT.budgets.map(function (b) {{ return '<span>' + b + '</span>'; }}).join('');
      $('budget').max = String(OPT.budgets.length - 1);
      $('budget').addEventListener('input', function () {{ activeRoute = null; drawOptimised(); }});
      $('equity-toggle').addEventListener('change', function () {{ activeRoute = null; drawOptimised(); }});
      $('tab-add').addEventListener('click', function () {{ setMode('add'); }});
    }}
    setMode('network');
  }}
  start();
}})();
"""


def build_interactive_map(
    scored: pd.DataFrame,
    stations: pd.DataFrame,
    metro_feed: dict[str, pd.DataFrame],
    out_path: Path,
    radius_m: int = 500,
    optimisation: dict | None = None,
) -> Path:
    """Write the standalone interactive atlas to `out_path`."""
    # control_scale stays off: Folium's renders dual km/mi. A metric-only scale
    # is added in JS instead -- a "3 mi" readout on a slide in Hyderabad is noise.
    # OpenStreetMap rather than a dark CartoDB style, which now needs an API key;
    # the CSS filter darkens it, and if tiles fail the page still reads.
    fmap = folium.Map(
        location=HYDERABAD_CENTRE,
        zoom_start=12,
        tiles="OpenStreetMap",
        control_scale=False,
        zoom_control=False,
    )
    for line in _metro_lines(metro_feed):
        folium.PolyLine(
            line["points"],
            color=line["colour"],
            weight=3,
            opacity=0.8,
            tooltip=f"Metro {line['route_id']}",
        ).add_to(fmap)

    payload = _station_payload(scored, stations, radius_m)
    band_order = [b for b in TIME_BANDS if b in payload["kpis"]]

    root = fmap.get_root()
    root.header.add_child(branca.element.Element(_css()))
    root.html.add_child(
        branca.element.Element(_html(band_order, optimisation is not None))
    )
    root.script.add_child(
        branca.element.Element(_script(payload, band_order, optimisation))
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(out_path))
    return out_path
