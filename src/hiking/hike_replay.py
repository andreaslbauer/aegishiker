"""Builds the persisted InteractiveHikeMap model and self-contained offline HTML replay page
for the 'I Hiked This Trail' / 'Re-Experience Hike' feature.
"""
import base64
import html
import json
from pathlib import Path
from typing import List, Optional

from .elevation import build_elevation_profile, summarize_elevation
from .models import DEMGrid, HikePhotoPin, InteractiveHikeMap, POI


def build_interactive_hike_map(
    session_id: str,
    title: str,
    coords_3d: List[List[float]],
    photo_pins: List[HikePhotoPin],
    pack_id: str = "",
    region_name: str = "",
    hiked_date: str = "",
    pois: Optional[List[POI]] = None,
    notes: str = "",
    dem: Optional[DEMGrid] = None,
    html_path: str = "",
) -> InteractiveHikeMap:
    """Construct a complete InteractiveHikeMap object from a traced 3D trail and matched photos."""
    profile = build_elevation_profile(coords_3d, dem=dem)
    summary = summarize_elevation(coords_3d, dem=dem)
    return InteractiveHikeMap(
        session_id=session_id,
        pack_id=pack_id,
        title=title,
        hiked_date=hiked_date,
        region_name=region_name,
        coords_3d=coords_3d,
        elevation_profile=profile,
        photo_pins=photo_pins,
        pois=list(pois or []),
        distance_m=summary["distance_m"],
        elevation_gain_m=summary["elevation_gain_m"],
        elevation_loss_m=summary["elevation_loss_m"],
        min_ele_m=summary["min_ele_m"],
        max_ele_m=summary["max_ele_m"],
        notes=notes,
        html_path=html_path,
    )


def _encode_photo_data_uri(pin: HikePhotoPin) -> str:
    """Return a data:image/... URI for a photo pin's thumbnail or image if readable on disk."""
    for candidate in (pin.thumb_path, pin.photo_path):
        if not candidate:
            continue
        p = Path(candidate)
        if p.exists() and p.is_file() and p.stat().st_size <= 5_000_000:
            ext = p.suffix.lower().lstrip(".")
            mime = "image/png" if ext == "png" else ("image/webp" if ext == "webp" else "image/jpeg")
            try:
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{b64}"
            except OSError:
                continue
    return ""


def render_interactive_hike_html(
    hike_map: InteractiveHikeMap,
    units: str = "us",
    embed_thumbnails: bool = True,
) -> str:
    """Render a self-contained, zero-external-dependency HTML file to re-experience the hike."""
    payload = hike_map.model_dump()
    payload["units"] = units
    if embed_thumbnails:
        for idx, pin in enumerate(hike_map.photo_pins):
            payload["photo_pins"][idx]["data_uri"] = _encode_photo_data_uri(pin)

    payload_json = json.dumps(payload, ensure_ascii=False)
    safe_title = html.escape(hike_map.title or "Interactive Hike Replay")
    safe_region = html.escape(hike_map.region_name or hike_map.pack_id or "AegisHiker")
    safe_date = html.escape(hike_map.hiked_date or hike_map.created_at[:10])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: file: 'self';">
<title>{safe_title} — AegisHiker Interactive Hike Replay</title>
<style>
  :root {{
    --bg: #0f1714;
    --panel: #17231e;
    --card: #1f2f28;
    --border: #2e463b;
    --text: #e8f2ec;
    --muted: #9ab3a6;
    --accent: #34d399;
    --amber: #fbbf24;
    --coral: #f87171;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }}
  header {{
    padding: 14px 22px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }}
  header h1 {{ font-size: 1.25rem; color: var(--accent); }}
  header .meta {{ font-size: 0.9rem; color: var(--muted); }}
  .stats-strip {{
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }}
  .stat-pill {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 0.85rem;
  }}
  .stat-pill strong {{ color: var(--amber); margin-left: 4px; }}
  main {{
    display: grid;
    grid-template-columns: 1fr 370px;
    gap: 16px;
    padding: 16px;
    flex: 1;
  }}
  @media (max-width: 920px) {{
    main {{ grid-template-columns: 1fr; }}
  }}
  .left-col {{
    display: flex;
    flex-direction: column;
    gap: 14px;
  }}
  .canvas-card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  canvas {{
    width: 100%;
    background: #121d18;
    border-radius: 8px;
    cursor: crosshair;
  }}
  #mapCanvas {{ height: 390px; }}
  #eleCanvas {{ height: 170px; }}
  .controls {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 4px;
  }}
  button {{
    background: var(--accent);
    color: #091510;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 700;
    cursor: pointer;
  }}
  button.secondary {{
    background: var(--card);
    color: var(--text);
    border: 1px solid var(--border);
  }}
  input[type=range] {{
    flex: 1;
    accent-color: var(--accent);
  }}
  .right-col {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-height: calc(100vh - 110px);
    overflow-y: auto;
  }}
  .photo-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    cursor: pointer;
    transition: border-color 0.15s;
  }}
  .photo-card.active {{
    border: 2px solid var(--amber);
  }}
  .photo-card img {{
    width: 100%;
    max-height: 210px;
    object-fit: cover;
    border-radius: 6px;
    margin-bottom: 8px;
  }}
  .badge {{
    display: inline-block;
    background: #273b32;
    color: var(--accent);
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 0.75rem;
    margin-right: 4px;
    margin-top: 4px;
  }}
</style>
</head>
<body>
<header>
  <div>
    <h1>{safe_title}</h1>
    <div class="meta">{safe_region} · Hiked {safe_date}</div>
  </div>
  <div class="stats-strip" id="statsStrip"></div>
</header>
<main>
  <div class="left-col">
    <div class="canvas-card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <strong>Interactive Trail &amp; Photo Pin Map</strong>
        <span id="livePosLabel" style="font-size:0.85rem;color:var(--muted);">Scrub or press Play to re-experience hike</span>
      </div>
      <canvas id="mapCanvas" width="900" height="420"></canvas>
      <div class="controls">
        <button id="playBtn" onclick="togglePlay()">▶ Play Hike</button>
        <input type="range" id="scrubber" min="0" max="1000" value="0" oninput="onScrub(this.value)">
        <button class="secondary" onclick="resetReplay()">↺ Reset</button>
      </div>
    </div>
    <div class="canvas-card">
      <strong>Elevation Profile &amp; Photo Markers</strong>
      <canvas id="eleCanvas" width="900" height="180"></canvas>
    </div>
  </div>
  <div class="right-col">
    <strong>Hike Story &amp; AegisPhotoVoyager Photos (<span id="photoCount">0</span>)</strong>
    <div id="notesBox" style="font-size:0.88rem;color:var(--muted);"></div>
    <div id="photoList" style="display:flex;flex-direction:column;gap:10px;"></div>
  </div>
</main>
<script>
const HIKE = {payload_json};
let progress = 0.0;
let playing = false;
let timerId = null;
let activePinIndex = -1;

function fmtDist(m) {{
  return HIKE.units === "us" ? (m / 1609.344).toFixed(2) + " mi" : (m / 1000.0).toFixed(2) + " km";
}}
function fmtEle(m) {{
  return HIKE.units === "us" ? Math.round(m * 3.28084).toLocaleString() + " ft" : Math.round(m).toLocaleString() + " m";
}}

function initPage() {{
  const strip = document.getElementById("statsStrip");
  strip.innerHTML = `
    <div class="stat-pill">Distance <strong>${{fmtDist(HIKE.distance_m)}}</strong></div>
    <div class="stat-pill">Elevation Gain <strong>+${{fmtEle(HIKE.elevation_gain_m)}}</strong></div>
    <div class="stat-pill">Summit / Max <strong>${{fmtEle(HIKE.max_ele_m)}}</strong></div>
    <div class="stat-pill">Photos Matched <strong>${{(HIKE.photo_pins || []).length}}</strong></div>
  `;
  document.getElementById("photoCount").textContent = (HIKE.photo_pins || []).length;
  document.getElementById("notesBox").textContent = HIKE.notes || "";
  renderPhotoCards();
  drawAll();
}}

function renderPhotoCards() {{
  const list = document.getElementById("photoList");
  const pins = HIKE.photo_pins || [];
  if (!pins.length) {{
    list.innerHTML = `<div style="color:var(--muted);font-size:0.9rem;">No geotagged photos matched along this trail corridor yet.</div>`;
    return;
  }}
  list.innerHTML = pins.map((p, idx) => {{
    const imgHtml = p.data_uri ? `<img src="${{p.data_uri}}" alt="${{p.filename}}">` : "";
    const tags = (p.ai_tags || []).map(t => `<span class="badge">#${{t}}</span>`).join("");
    const people = (p.people || []).map(pe => `<span class="badge">👤 ${{pe}}</span>`).join("");
    return `
      <div class="photo-card ${{idx === activePinIndex ? 'active' : ''}}" id="pinCard_${{idx}}" onclick="selectPin(${{idx}})">
        ${{imgHtml}}
        <div style="display:flex;justify-content:space-between;font-size:0.82rem;color:var(--amber);">
          <span>📷 #${{idx + 1}} · ${{p.filename}}</span>
          <span>${{fmtDist(p.distance_along_m)}} · ${{fmtEle(p.ele_m)}}</span>
        </div>
        <div style="font-size:0.88rem;margin-top:4px;">${{p.caption || p.ai_summary || ''}}</div>
        <div style="font-size:0.76rem;color:var(--muted);margin-top:3px;">${{p.date_iso || ''}} ${{p.camera_model ? '· ' + p.camera_model : ''}}</div>
        <div>${{people}}${{tags}}</div>
      </div>
    `;
  }}).join("");
}}

function selectPin(idx) {{
  const pins = HIKE.photo_pins || [];
  if (idx < 0 || idx >= pins.length) return;
  activePinIndex = idx;
  if (HIKE.distance_m > 0) {{
    progress = Math.max(0, Math.min(1, pins[idx].distance_along_m / HIKE.distance_m));
    document.getElementById("scrubber").value = Math.round(progress * 1000);
  }}
  renderPhotoCards();
  const el = document.getElementById("pinCard_" + idx);
  if (el) el.scrollIntoView({{ behavior: "smooth", block: "nearest" }});
  drawAll();
}}

function onScrub(val) {{
  progress = Math.max(0, Math.min(1, Number(val) / 1000.0));
  syncNearestPin();
  drawAll();
}}

function syncNearestPin() {{
  const pins = HIKE.photo_pins || [];
  if (!pins.length || HIKE.distance_m <= 0) return;
  const targetM = progress * HIKE.distance_m;
  let best = -1;
  let bestDiff = 1e9;
  pins.forEach((p, idx) => {{
    const d = Math.abs(p.distance_along_m - targetM);
    if (d < bestDiff && d <= Math.max(200, HIKE.distance_m * 0.06)) {{
      bestDiff = d;
      best = idx;
    }}
  }});
  if (best !== -1 && best !== activePinIndex) {{
    activePinIndex = best;
    renderPhotoCards();
  }}
}}

function togglePlay() {{
  playing = !playing;
  document.getElementById("playBtn").textContent = playing ? "⏸ Pause" : "▶ Play Hike";
  if (playing) {{
    if (progress >= 0.995) progress = 0.0;
    timerId = setInterval(() => {{
      progress = Math.min(1.0, progress + 0.008);
      document.getElementById("scrubber").value = Math.round(progress * 1000);
      syncNearestPin();
      drawAll();
      if (progress >= 1.0) togglePlay();
    }}, 60);
  }} else if (timerId) {{
    clearInterval(timerId);
    timerId = null;
  }}
}}

function resetReplay() {{
  if (playing) togglePlay();
  progress = 0.0;
  activePinIndex = -1;
  document.getElementById("scrubber").value = 0;
  renderPhotoCards();
  drawAll();
}}

function currentProfilePoint() {{
  const prof = HIKE.elevation_profile || [];
  if (!prof.length) return {{ distance_m: 0, ele_m: 0, lat: 0, lon: 0, grade_pct: 0 }};
  const targetM = progress * HIKE.distance_m;
  for (let i = 1; i < prof.length; i++) {{
    if (prof[i].distance_m >= targetM) {{
      const p0 = prof[i - 1], p1 = prof[i];
      const span = Math.max(0.1, p1.distance_m - p0.distance_m);
      const t = Math.max(0, Math.min(1, (targetM - p0.distance_m) / span));
      return {{
        distance_m: targetM,
        ele_m: p0.ele_m + t * (p1.ele_m - p0.ele_m),
        lat: p0.lat + t * (p1.lat - p0.lat),
        lon: p0.lon + t * (p1.lon - p0.lon),
        grade_pct: p1.grade_pct
      }};
    }}
  }}
  return prof[prof.length - 1];
}}

function drawAll() {{
  drawMap();
  drawElevation();
  const cur = currentProfilePoint();
  document.getElementById("livePosLabel").textContent =
    `${{fmtDist(cur.distance_m)}} · Alt ${{fmtEle(cur.ele_m)}} · Grade ${{cur.grade_pct.toFixed(1)}}%`;
}}

function drawMap() {{
  const canvas = document.getElementById("mapCanvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const pts = HIKE.coords_3d || [];
  if (!pts.length) return;

  let minLon = 1e9, maxLon = -1e9, minLat = 1e9, maxLat = -1e9;
  pts.forEach(p => {{
    minLon = Math.min(minLon, p[0]); maxLon = Math.max(maxLon, p[0]);
    minLat = Math.min(minLat, p[1]); maxLat = Math.max(maxLat, p[1]);
  }});
  const pad = 42;
  const spanLon = Math.max(0.002, maxLon - minLon);
  const spanLat = Math.max(0.002, maxLat - minLat);
  const toXY = (lon, lat) => [
    pad + ((lon - minLon) / spanLon) * (w - 2 * pad),
    h - pad - ((lat - minLat) / spanLat) * (h - 2 * pad)
  ];

  // Topo grid lines
  ctx.strokeStyle = "#1d2e26";
  ctx.lineWidth = 1;
  for (let i = 1; i < 6; i++) {{
    ctx.beginPath(); ctx.moveTo(0, (h / 6) * i); ctx.lineTo(w, (h / 6) * i); ctx.stroke();
    ctx.beginPath(); ctx.moveTo((w / 6) * i, 0); ctx.lineTo((w / 6) * i, h); ctx.stroke();
  }}

  // Trail polyline
  ctx.strokeStyle = "#34d399";
  ctx.lineWidth = 3.5;
  ctx.beginPath();
  pts.forEach((p, idx) => {{
    const [x, y] = toXY(p[0], p[1]);
    if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }});
  ctx.stroke();

  // Photo pins
  (HIKE.photo_pins || []).forEach((pin, idx) => {{
    const [px, py] = toXY(pin.lon, pin.lat);
    ctx.fillStyle = idx === activePinIndex ? "#fbbf24" : "#38bdf8";
    ctx.beginPath();
    ctx.arc(px, py, idx === activePinIndex ? 9 : 6.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#091510";
    ctx.font = "bold 10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(String(idx + 1), px, py + 3.5);
  }});

  // Animated hiker cursor
  const cur = currentProfilePoint();
  const [hx, hy] = toXY(cur.lon, cur.lat);
  ctx.fillStyle = "#f87171";
  ctx.beginPath();
  ctx.arc(hx, hy, 8, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.stroke();
}}

function drawElevation() {{
  const canvas = document.getElementById("eleCanvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const prof = HIKE.elevation_profile || [];
  if (prof.length < 2) return;

  const padL = 48, padR = 20, padT = 18, padB = 26;
  const maxD = Math.max(1, HIKE.distance_m);
  const minE = HIKE.min_ele_m;
  const maxE = Math.max(minE + 50, HIKE.max_ele_m);
  const toXY = (d, e) => [
    padL + (d / maxD) * (w - padL - padR),
    h - padB - ((e - minE) / (maxE - minE)) * (h - padT - padB)
  ];

  ctx.beginPath();
  prof.forEach((p, idx) => {{
    const [x, y] = toXY(p.distance_m, p.ele_m);
    if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }});
  ctx.strokeStyle = "#34d399";
  ctx.lineWidth = 2.5;
  ctx.stroke();

  // Photo pins on elevation curve
  (HIKE.photo_pins || []).forEach((pin, idx) => {{
    const [px, py] = toXY(pin.distance_along_m, pin.ele_m);
    ctx.fillStyle = idx === activePinIndex ? "#fbbf24" : "#38bdf8";
    ctx.beginPath();
    ctx.arc(px, py, 5, 0, Math.PI * 2);
    ctx.fill();
  }});

  // Current scrubber cursor
  const cur = currentProfilePoint();
  const [cx, cy] = toXY(cur.distance_m, cur.ele_m);
  ctx.strokeStyle = "#fbbf24";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(cx, padT);
  ctx.lineTo(cx, h - padB);
  ctx.stroke();
  ctx.fillStyle = "#f87171";
  ctx.beginPath();
  ctx.arc(cx, cy, 6, 0, Math.PI * 2);
  ctx.fill();
}}

window.addEventListener("DOMContentLoaded", initPage);
</script>
</body>
</html>
"""


def build_interactive_map_data(
    route: object,
    session: Optional[object] = None,
    photos: Optional[List[HikePhotoPin]] = None,
    destination_name: str = "",
) -> dict:
    """Build a JSON-serializable dictionary representing an InteractiveHikeMap."""
    r_coords = getattr(route, "coords_3d", [])
    s_id = getattr(session, "id", None) or getattr(route, "id", "session_1")
    h_date = (getattr(session, "started_at", "") or "")[:10]
    notes = getattr(session, "notes", "") or getattr(route, "notes", "")
    pack_id = getattr(route, "pack_id", "")
    title = getattr(route, "name", "Recorded Hike")
    hike_map = build_interactive_hike_map(
        session_id=str(s_id),
        title=str(title),
        coords_3d=list(r_coords),
        photo_pins=list(photos or []),
        pack_id=str(pack_id),
        region_name=destination_name or str(pack_id),
        hiked_date=h_date,
        notes=str(notes),
    )
    return hike_map.model_dump()


def export_interactive_hike_html(
    map_data: dict | InteractiveHikeMap,
    output_path: str | Path,
    units: str = "metric",
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    hike_map = (
        map_data
        if isinstance(map_data, InteractiveHikeMap)
        else InteractiveHikeMap.model_validate(map_data)
    )
    html_str = render_interactive_hike_html(hike_map, units=units)
    out.write_text(html_str, encoding="utf-8")
    return out

