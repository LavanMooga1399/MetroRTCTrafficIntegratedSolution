"""Interactive Transfer Gap Atlas map.

The point of this map is not to show where the metro stations are. It is to let
someone drag a slider from evening peak to late night and watch the bus network
around those stations go out, corridor by corridor.

That requires the slider to swap each station's underlying metrics rather than
toggling five pre-rendered layers, so the markers resize and recolour in place
and the eye tracks the change. Folium supplies the base map and the metro
lines; a small amount of Leaflet-level JavaScript does the rest, which is far
less code than fighting TimestampedGeoJson into behaving this way.
"""

from __future__ import annotations

import json
from pathlib import Path

import branca
import folium
import pandas as pd

from .connectivity import TIME_BANDS

BAND_LABELS = {
    "morning_peak": "07:00 – 10:00  Morning peak",
    "midday": "10:00 – 16:00  Midday",
    "evening_peak": "16:00 – 20:00  Evening peak",
    "night": "20:00 – 23:00  Night",
    "late_night": "23:00 – 04:00  Late night",
}

# Warm where service holds up, red where it has gone. Chosen to read on a dark
# base map and to stay distinguishable for the most common colour deficiencies.
GRADE_COLOURS = {
    "maintained": "#4ade80",
    "reduced": "#a3e635",
    "poor": "#fbbf24",
    "collapsed": "#fb7185",
    "no_service": "#ef4444",
}

HYDERABAD_CENTRE = (17.42, 78.47)


def _metro_lines(metro_feed: dict[str, pd.DataFrame]) -> list[dict]:
    """Corridor polylines with their official route colours."""
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
    """Per-station metrics for every band, plus the KPI roll-up per band."""
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
        served = b[b["departures_per_hour"] > 0]
        kpis[band] = {
            "served": int(len(served)),
            "total": int(len(b)),
            "median_dep": round(float(b["departures_per_hour"].median()), 1),
            "median_dest": int(b["n_destinations"].median()),
            "retention": round(float(b["retention"].median()), 4),
            "zero": int((b["departures_per_hour"] == 0).sum()),
        }

    return {"stations": by_station, "kpis": kpis}


def _panel_css() -> str:
    return """
<style>
  html, body, #map, .folium-map { background: #0b0d13 !important; }
  /* Darken the OSM raster client-side; keeps the basemap key-free while the
     markers and metro lines stay saturated on top of it. */
  .leaflet-tile-pane { filter: invert(1) hue-rotate(185deg) brightness(0.92)
                               contrast(0.88) saturate(0.55); }
  .leaflet-control-attribution { background: rgba(17,20,28,0.85) !important;
    color: #7c8497 !important; }
  .leaflet-control-attribution a { color: #9aa2b6 !important; }
  .tga-panel, .tga-legend {
    position: absolute; z-index: 9999;
    background: rgba(17, 20, 28, 0.92);
    color: #e8eaf0; border: 1px solid rgba(255,255,255,0.13);
    border-radius: 10px; padding: 14px 16px;
    font: 13px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    box-shadow: 0 8px 28px rgba(0,0,0,0.45);
  }
  .tga-panel { top: 14px; left: 14px; width: 310px; }
  /* Bottom-RIGHT, clear of the scale bar at bottom-left and sitting above the
     attribution line. On a narrow projector the legend and the scale bar
     collided when both were on the left. */
  .tga-legend { bottom: 34px; right: 14px; width: 190px; padding: 12px 14px; }
  .tga-title { font-size: 12px; letter-spacing: .09em; text-transform: uppercase;
    color: #8b93a7; margin: 0 0 2px; }
  .tga-band { font-size: 19px; font-weight: 600; margin: 0 0 12px; color: #fff; }
  .tga-kpi { display: flex; justify-content: space-between; align-items: baseline;
    padding: 5px 0; border-top: 1px solid rgba(255,255,255,0.07); }
  .tga-kpi span:first-child { color: #9aa2b6; }
  .tga-kpi span:last-child { font-variant-numeric: tabular-nums;
    font-weight: 600; font-size: 15px; }
  .tga-alert { color: #fb7185 !important; }
  .tga-slider { width: 100%; margin: 14px 0 4px; accent-color: #60a5fa; }
  .tga-ticks { display: flex; justify-content: space-between;
    font-size: 10px; color: #6b7386; }
  .tga-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
  .tga-dot { width: 11px; height: 11px; border-radius: 50%; flex: none; }
  .tga-note { margin-top: 10px; font-size: 11px; color: #6b7386; line-height: 1.4; }
  .leaflet-popup-content-wrapper { background: #11141c; color: #e8eaf0;
    border-radius: 8px; }
  .leaflet-popup-tip { background: #11141c; }
  .tga-pop-name { font-size: 15px; font-weight: 600; margin-bottom: 6px; }
  .tga-pop td { padding: 2px 10px 2px 0; font-size: 12.5px; }
  .tga-pop td:last-child { font-variant-numeric: tabular-nums; font-weight: 600; }
</style>
"""


def _panel_html(band_order: list[str]) -> str:
    ticks = "".join(
        f"<span>{lbl}</span>"
        for lbl in ("Morning", "Midday", "Evening", "Night", "Late")
    )
    return f"""
<div class="tga-panel">
  <p class="tga-title">Hyderabad metro–bus connectivity</p>
  <p class="tga-band" id="tga-band">—</p>
  <input class="tga-slider" id="tga-slider" type="range"
         min="0" max="{len(band_order) - 1}" step="1" value="2">
  <div class="tga-ticks">{ticks}</div>
  <div class="tga-kpi"><span>Stations with any bus</span><span id="tga-served">—</span></div>
  <div class="tga-kpi"><span>Median departures / hour</span><span id="tga-dep">—</span></div>
  <div class="tga-kpi"><span>Median destinations reachable</span><span id="tga-dest">—</span></div>
  <div class="tga-kpi"><span>Peak service retained</span><span id="tga-ret">—</span></div>
  <p class="tga-note">Bus service within 500 m of each metro station.
     Marker size is departures per hour. Open GTFS, TGSRTC + HMRL.</p>
</div>
<div class="tga-legend">
  <p class="tga-title" style="margin-bottom:6px">Service retained</p>
  <div class="tga-row"><span class="tga-dot" style="background:#4ade80"></span>Maintained &gt;40%</div>
  <div class="tga-row"><span class="tga-dot" style="background:#a3e635"></span>Reduced 15–40%</div>
  <div class="tga-row"><span class="tga-dot" style="background:#fbbf24"></span>Poor 5–15%</div>
  <div class="tga-row"><span class="tga-dot" style="background:#fb7185"></span>Collapsed 1–5%</div>
  <div class="tga-row"><span class="tga-dot" style="background:#ef4444"></span>No service</div>
</div>
"""


def _script(payload: dict, band_order: list[str], labels: dict, colours: dict) -> str:
    """Bare JS, no <script> wrapper: Folium appends this inside its own block."""
    return f"""
(function () {{
  var DATA = {json.dumps(payload)};
  var BANDS = {json.dumps(band_order)};
  var LABELS = {json.dumps(labels)};
  var COLOURS = {json.dumps(colours)};
  var map = null, layer = null;

  // Folium names its map variable unpredictably AND emits this block before the
  // one that creates the map, so we poll for it rather than reading it now.
  function findMap() {{
    var keys = Object.keys(window);
    for (var i = 0; i < keys.length; i++) {{
      var k = keys[i];
      if (k.indexOf('map_') === 0 && window[k] && window[k]._container) {{
        return window[k];
      }}
    }}
    return null;
  }}

  function radius(dep) {{
    // Square root keeps a 300/h station from swamping a 20/h one; the cap stops
    // peak markers merging into one blob over the corridors, and the floor
    // keeps dead stations visible rather than vanishing silently.
    return dep <= 0 ? 4.5 : Math.max(4.5, Math.min(15, 1.6 * Math.sqrt(dep)));
  }}

  function popup(name, m) {{
    return '<div class="tga-pop">' +
      '<div class="tga-pop-name">' + name + '</div>' +
      '<table><tr><td>Bus departures / hour</td><td>' + m.dep + '</td></tr>' +
      '<tr><td>Destinations reachable</td><td>' + m.dest + '</td></tr>' +
      '<tr><td>Distinct routes</td><td>' + m.routes + '</td></tr>' +
      '<tr><td>Peak service retained</td><td>' + (m.ret * 100).toFixed(1) + '%</td></tr>' +
      '<tr><td>Nearest bus stop</td><td>' +
        (m.walk === null ? 'none within 500 m' : m.walk + ' m') + '</td></tr>' +
      '</table></div>';
  }}

  function draw(bandIndex) {{
    var band = BANDS[bandIndex];
    layer.clearLayers();

    Object.keys(DATA.stations).forEach(function (id) {{
      var s = DATA.stations[id];
      var m = s.bands[band];
      if (!m) {{ m = {{dep: 0, dest: 0, routes: 0, ret: 0, walk: null, grade: 'no_service'}}; }}
      var colour = COLOURS[m.grade] || '#ef4444';
      var dead = m.dep <= 0;

      L.circleMarker([s.lat, s.lon], {{
        radius: radius(m.dep),
        color: dead ? '#ffffff' : colour,
        weight: dead ? 2 : 1,
        opacity: dead ? 0.95 : 0.85,
        fillColor: colour,
        fillOpacity: dead ? 0.95 : 0.6
      }}).bindPopup(popup(s.name, m))
        .bindTooltip(s.name + ' — ' + m.dep + '/h', {{direction: 'top'}})
        .addTo(layer);

      // A station that has lost its bus network entirely gets a halo, so the
      // eye finds it without having to read every marker.
      if (dead) {{
        L.circleMarker([s.lat, s.lon], {{
          radius: 15, color: '#ef4444', weight: 1.5,
          opacity: 0.85, fill: false
        }}).addTo(layer);
      }}
    }});

    var k = DATA.kpis[band] || {{}};
    document.getElementById('tga-band').textContent = LABELS[band];
    document.getElementById('tga-served').textContent =
      (k.served != null ? k.served + ' / ' + k.total : '—');
    document.getElementById('tga-dep').textContent =
      (k.median_dep != null ? k.median_dep : '—');
    document.getElementById('tga-dest').textContent =
      (k.median_dest != null ? k.median_dest : '—');
    document.getElementById('tga-ret').textContent =
      (k.retention != null ? (k.retention * 100).toFixed(1) + '%' : '—');

    var alert = k.retention != null && k.retention < 0.15;
    ['tga-dep', 'tga-dest', 'tga-ret', 'tga-served'].forEach(function (id) {{
      document.getElementById(id).classList.toggle('tga-alert', alert);
    }});
  }}

  function start() {{
    map = findMap();
    if (!map) {{ return window.setTimeout(start, 60); }}
    layer = L.layerGroup().addTo(map);
    L.control.scale({{imperial: false, position: 'bottomleft'}}).addTo(map);
    var slider = document.getElementById('tga-slider');
    slider.addEventListener('input', function () {{ draw(+this.value); }});
    draw(+slider.value);
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
) -> Path:
    """Write the standalone interactive atlas to `out_path`."""
    # OpenStreetMap rather than a dark CartoDB style: those now require an API
    # key, and the venue is expected to have poor or no wifi. OSM tiles are
    # key-free, and the CSS filter below darkens them client-side. If tiles fail
    # to load entirely, the dark page background stays and the metro lines and
    # station markers still read -- the demo degrades instead of dying.
    # control_scale stays off: Folium's version renders dual km/mi units. The
    # JS below adds a metric-only scale instead -- a "3 mi" readout on a slide
    # in Hyderabad is noise.
    fmap = folium.Map(
        location=HYDERABAD_CENTRE,
        zoom_start=12,
        tiles="OpenStreetMap",
        control_scale=False,
    )

    for line in _metro_lines(metro_feed):
        folium.PolyLine(
            line["points"],
            color=line["colour"],
            weight=3.5,
            opacity=0.75,
            tooltip=f"Metro {line['route_id']}",
        ).add_to(fmap)

    payload = _station_payload(scored, stations, radius_m)
    band_order = [b for b in TIME_BANDS if b in payload["kpis"]]

    root = fmap.get_root()
    root.header.add_child(branca.element.Element(_panel_css()))
    root.html.add_child(branca.element.Element(_panel_html(band_order)))
    root.script.add_child(
        branca.element.Element(
            _script(payload, band_order, BAND_LABELS, GRADE_COLOURS)
        )
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(out_path))
    return out_path
