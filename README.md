# AegisHiker

**Offline-First Trail Brainstormer, Route Composer, Photo Matcher & Mobile Companion**  
*Part of the Aegis Application Family*

AegisHiker is a privacy-preserving, local-first desktop and mobile companion application for planning hikes, composing loops and traverses with elevation-aware pacing, matching geotagged photos from **AegisPhotoVoyager**, re-experiencing completed hikes on an interactive map, and navigating offline on the trail via a zero-signal Mobile Web Companion PWA or Native iOS SwiftUI app.

---

## Key Features

1. **8 Bundled Regional Destination Packs + Opt-In Region Downloader**
   - Ships with 8 offline starter destination packs in `data/packs/`:
     - **New Hampshire White Mountains (`white_mountains_nh`)** — 37 trails (30 named OSM trails from `data/osm_trails.json`, e.g. Crawford Path, Twinway, Edmands Path, Huntington Ravine) plus Franconia Ridge Loop, Mount Lafayette, Mount Lincoln, Little Haystack, Mount Washington (Tuckerman Ravine & Lion Head), AMC Greenleaf & Lakes of the Clouds Huts
     - **Yosemite Valley & High Country (`yosemite_valley`)**
     - **Acadia National Park (`acadia_np`)**
     - **Shenandoah National Park — Central (`shenandoah_central`)**
     - **Rocky Mountain National Park — Bear Lake Corridor (`rocky_mountain_bear_lake`)**
     - **Zion National Park — Main Canyon (`zion_canyon`)**
     - **Mount Rainier — Paradise Corridor (`mount_rainier_paradise`)**
     - **Mount Monadnock (`mount_monadnock`)** — every named trail on the mountain from OpenStreetMap (White Dot, White Cross, White Arrow, Pumpelly, Dublin, Marlboro-side Metacomet-Monadnock, Smith Summit, Spellman, ...)
     - **Fitchburg, Leominster & Princeton (`fitchburg_state_forest`)** — every named trail from Fitchburg State Forest, Pearl Hill and Willard Brook south through Leominster State Forest (Crow Hill Ledges) to Princeton and Wachusett Mountain, plus the Midstate Trail across the area in five sections
     - **Acton, Boxborough & Harvard (`acton_boxborough_harvard`)** — every named trail on the three towns' conservation lands from OpenStreetMap (Nashoba Brook, Great Hill, Steele Farm, Beaver Brook, Prospect Hill, Holy Hill, Oxbow NWR, ...)
     - **Pioneer Valley (`pioneer_valley`)** — every named trail in the Connecticut River valley from OpenStreetMap: Mt. Tom, the Holyoke Range (Mt. Holyoke, Norwottuck, Horse Caves), Northampton, Amherst, Mt. Sugarloaf and Mt. Toby, with the New England (Metacomet-Monadnock) and Robert Frost trails
     - **Great Smoky Mountains National Park (`great_smoky_mountains`)**, **Grand Canyon National Park (`grand_canyon`)**, **Yellowstone National Park (`yellowstone`)**, **Grand Teton National Park (`grand_teton`)** and **Joshua Tree National Park (`joshua_tree`)** — each covers the whole park, with every named trail from OpenStreetMap and the main summits, trailheads, visitor centers, waterfalls and viewpoints
   - Opt-in **Download More Regions** manager fetches additional regional packs on demand and verifies SHA-256 digests before importing into local SQLite (`~/.aegishiker/aegishiker.db`).
   - **Real map detail** in every pack (`src/core/map_details.py`): shaded relief and 200 ft contours from AWS Terrain Tiles (USGS 3DEP), plus OpenStreetMap paths, tracks, roads, streams, lakes, cliffs, scree, wetlands, peaks, saddles, springs, drinking water, huts, shelters, campsites, viewpoints, waterfalls, parking, trailheads, toilets and signposts. Starter-pack trails are re-routed onto the real OSM trail network and re-elevated from the terrain; their POIs are moved onto the matching OSM feature. Refresh with `.venv/bin/python tools/fetch_map_details.py [pack_id ...]` (run it after `tools/build_starter_packs.py`, which writes trail-only packs). Region downloads fetch the same detail automatically.

2. **Interactive Trail Composer & Elevation-Aware Pace Modeling**
   - A* trail graph routing (`src/hiking/routing.py`) across trail junctions, summits, water sources, shelters, and viewpoints.
   - Modified **Naismith + Tobler + Tranter** pace engine (`src/hiking/pace.py`) accounting for elevation gain, steep descent penalties, pack weight, surface technicality, altitude above 2,400 m, and hiker fitness level.
   - Custom `QPainter` vector topographic map canvas (`src/gui/charts/map_canvas.py`) with drag-to-pan, cursor-anchored zoom, scale bar, and hover details for trails, water sources and shelters, interactive elevation profile with grade color-coding (`src/gui/charts/elevation_chart.py`), and split pace chart (`src/gui/charts/pace_chart.py`).

3. **"I Hiked This Trail" Workflow & AegisPhotoVoyager Integration (`src/core/voyager_bridge.py`)**
   - Select any region, trace your hiked trail on the interactive map (or snap through trail junctions), and click **"✓ I Hiked This Trail"**.
   - Automatically discovers **AegisPhotoVoyager**'s SQLite database (`~/.AegisPhotoVoyager/voyager.db`, `photos.db`, `$SNAP_USER_COMMON/.AegisPhotoVoyager/voyager.db`, `QSettings`, or a user-selected file) in read-only mode (`?mode=ro`).
   - Matches geotagged photos (`photos.lat`, `photos.lon`, `photos.date_taken`, plus AI summaries, tags, mood, and recognized faces) within your chosen corridor radius (`150m` default) and snaps each photo to its exact kilometer post and elevation along the trail.
   - Persists an interactive hike replay map (`~/.aegishiker/maps/<session_id>.html` + SQLite `hike_sessions.interactive_map_data`) and opens the **Re-Experience Hike** tab with an animated hiker scrubber, elevation crosshairs, and clickable photo pins.

   - **Start from the trailhead**: Explore's search also finds trailheads, parking lots and summits; *Start Hike Here* begins a segment-by-segment hike in the Composer from that spot. Saving it offers to log it as hiked straight away.
   - **Review photos**: a thumbnail review (`src/gui/dialogs/photo_review_dialog.py`) when logging a hike and for saved hikes; photos left out stay out when AegisPhotoVoyager is rescanned (the files are never touched). Hike details record who came along and the weather (optional Open-Meteo lookup, `src/core/weather.py`).
   - **Hike websites** (`src/core/hike_site.py`, templates in `src/web/hikesite/`): *Create Website…* writes a static folder with a summary page (date, times, area, people, weather, distance, climb, high point, trails taken with distance per trail), *Play the Hike* (a browser port of the 3D replay), a photo gallery with each photo's time, place on the trail and locator map, and an interactive topographic map with the elevation profile. Optional password protection encrypts the data and every photo (AES-256-GCM, PBKDF2) and decrypts it in the browser; *Upload Website* publishes it over FTP/FTPS (`src/core/ftp_upload.py`; the FTP password is never stored).

4. **Hybrid Offline Mobile Companion (Mobile Web PWA + Native SwiftUI iOS)**
   - **Mobile Web Companion PWA (`src/web/hiker.html`, `sw.js`)**: Pair your phone over local Wi-Fi via QR code (`src/core/trail_server.py`). Cache destination packs and active routes in IndexedDB for 100% zero-cell-signal usage at the trailhead. Includes live trailhead condition filtering (`sunny`, `rainy`, `icy`, `late_start`, `tired_legs`), live GPS tracking, off-trail distance alerts (`>45m`), and one-tap telemetry sync back to the desktop.
   - **Native iOS App (`ios/AegisHiker/`)**: Full Swift 5.10 / SwiftUI / MapKit / CoreLocation target with offline pack storage, live breadcrumb recording, off-trail haptics, and Bonjour/LAN desktop sync.

5. **Local Ollama AI Trail Guide (`qwen2.5:7b`) with Deterministic Fallback**
   - 7-stage Guided Trail Brainstorm Interview (`src/core/interview_engine.py`) and 12-tool Trail Guide Agent (`src/core/agent_engine.py`, `src/core/agent_tools.py`).
   - All AI modifications to your route or gear list require explicit confirmation via the diff preview dialog (`src/gui/dialogs/proposal_dialog.py`).
   - Works completely offline without Ollama using deterministic rule-based route synthesis and safety recommendations.

---

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"

# Launch the desktop application
.venv/bin/python main.py

# Run the headless end-to-end self-test
QT_QPA_PLATFORM=offscreen .venv/bin/python main.py --test-mode

# Run the unit & integration test suite
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -v
```

