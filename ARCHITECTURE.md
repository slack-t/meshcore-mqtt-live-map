# Architecture Guide

This document explains how the Mesh Live Map codebase is organized and how the components interact.
Current version: `1.2.0` (see `VERSIONS.md`).

## High-Level Overview

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│   MQTT Broker   │────▶│   Backend    │────▶│    Frontend     │
│  (meshcore/#)   │     │  (FastAPI)   │     │   (Leaflet)     │
└─────────────────┘     └──────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌──────────┐
                        │  State   │
                        │ (files)  │
                        └──────────┘
```

**Data flow:**
1. MQTT broker publishes MeshCore packets
2. Backend subscribes, decodes packets, extracts coordinates
3. Backend broadcasts updates via WebSocket to connected clients
4. Frontend renders nodes, routes, and heatmaps on a Leaflet map

---

## Directory Structure

```
mesh-live-map-dev/
├── backend/
│   ├── app.py              # FastAPI app, MQTT lifecycle, WebSocket, API routes
│   ├── config.py           # Environment variable loading (65+ settings)
│   ├── state.py            # Shared in-memory state (devices, routes, etc.)
│   ├── decoder.py          # Payload parsing, MeshCore decoding
│   ├── history.py          # Route history persistence (24h rolling window)
│   ├── los.py              # Line-of-sight calculations, elevation API
│   ├── turnstile.py        # Cloudflare Turnstile verification + tokens
│   ├── routes/             # HTTP/WebSocket route modules
│   │   ├── api.py           # API endpoints
│   │   ├── websocket.py     # WebSocket handlers
│   │   ├── static.py        # Static/HTML routes
│   │   └── debug.py         # Debug endpoints (dev only)
│   ├── services/           # Background services
│   │   ├── mqtt.py          # MQTT client setup + handlers
│   │   ├── broadcaster.py   # WebSocket broadcaster loop
│   │   ├── reaper.py        # Stale cleanup loop
│   │   └── persistence.py   # State + history persistence
│   ├── scripts/
│   │   └── meshcore_decode.mjs  # Node.js MeshCore packet decoder
│   ├── static/
│   │   ├── index.html      # HTML shell with template placeholders
│   │   ├── app.js          # All frontend logic (Leaflet, WebSocket, UI)
│   │   ├── styles.css      # UI styling
│   │   ├── landing.html    # Turnstile landing/verification page
│   │   ├── turnstile.js    # Turnstile widget + verification flow
│   │   ├── sw.js           # PWA service worker
│   │   └── logo.png        # Site branding
│   ├── Dockerfile          # Container build
│   └── requirements.txt    # Python dependencies
├── data/                   # Runtime state (created at first run)
│   ├── state.json          # Persisted devices, trails, names
│   ├── route_history.jsonl # Rolling route history
│   ├── device_roles.json   # Optional role overrides
│   └── neighbor_overrides.json # Optional neighbor overrides
├── docker-compose.yaml     # Container orchestration
├── .env.example            # Configuration template
├── pyproject.toml          # Python tooling (ruff, pytest)
├── .eslintrc.json          # JavaScript linting
├── README.md               # User-facing documentation
├── CONTRIBUTING.md         # Contributor guidelines
├── VERSIONS.md             # Changelog
└── docs.md                 # Implementation notes
```

---

## Backend Components

### app.py (Main Application)

The central module containing:

| Section | Lines | Purpose |
|---------|-------|---------|
| Imports & setup | 1-170 | Dependencies, FastAPI app creation |
| Helper functions | 170-430 | Payload serialization, token auth, git checks |
| MQTT handlers | 430-810 | `mqtt_on_connect`, `mqtt_on_message` |
| Broadcaster | 816-1028 | Async queue processing, WebSocket broadcasting |
| Reaper | 1030-1117 | Stale device/route cleanup |
| API routes | 1120-1520 | HTTP endpoints |
| Startup/shutdown | 1577-1640 | MQTT connection, background tasks |

**Key async tasks started at startup:**
- `broadcaster()` - Processes update queue, broadcasts to WebSocket clients
- `reaper()` - Cleans up stale devices/routes every 5 seconds
- `_state_saver()` - Persists state.json periodically
- `_route_history_saver()` - Persists route_history.jsonl
- `_git_check_loop()` - Checks for upstream updates

### config.py (Configuration)

Loads all settings from environment variables with sensible defaults.

**Key configuration groups:**
- MQTT connection (`MQTT_HOST`, `MQTT_PORT`, `MQTT_TLS`, etc.)
- State persistence (`STATE_DIR`, `STATE_SAVE_INTERVAL`)
- Neighbor overrides (`NEIGHBOR_OVERRIDES_FILE`)
- Device management (`DEVICE_TTL_SECONDS`, `TRAIL_LEN`)
- Route handling (`ROUTE_TTL_SECONDS`, `ROUTE_HISTORY_HOURS`)
- Turnstile protection (`TURNSTILE_*`, gated by `PROD_MODE=true`)
- Map display (`MAP_START_LAT`, `MAP_START_LON`, `MAP_RADIUS_KM`)
- Site metadata (`SITE_TITLE`, `SITE_DESCRIPTION`)

### state.py (Shared State)

In-memory data structures shared across modules:

```python
devices: Dict[str, DeviceState]     # Current device positions
trails: Dict[str, List]             # Position history per device
routes: Dict[str, Dict]             # Active route visualizations
heat_events: List[Dict]             # Recent activity points
route_history_segments: List[Dict]  # 24h route history
route_history_edges: Dict[str, Dict]# Aggregated edge counts
neighbor_edges: Dict[str, Dict]     # Neighbor adjacency cache
```

### decoder.py (Packet Parsing)

Handles multiple payload formats:
1. **Direct JSON coordinates** - `{"lat": 42.36, "lon": -71.05}`
2. **Text patterns** - `"lat 42.36 lon -71.05"`
3. **MeshCore packets** - Hex-encoded, decoded via Node.js

The Node.js decoder (`scripts/meshcore_decode.mjs`) uses the `@michaelhart/meshcore-decoder` package.

### history.py (Route History)

Maintains a 24-hour rolling window of route segments:
- Stored as JSONL (JSON Lines) for append-only writes
- Compacted periodically to remove old entries
- Aggregated into edges with counts for visualization

### los.py (Line of Sight)

Calculates terrain-based line of sight:
- Fetches elevation data from OpenTopoData API
- Samples points along the path
- Finds peaks and obstructions
- Suggests relay points when blocked

---

## Frontend Components

### app.js (Main JavaScript)

A single file containing all client-side logic:

| Section | Approximate Lines | Purpose |
|---------|-------------------|---------|
| Config parsing | 1-100 | URL params, localStorage, env vars |
| Map setup | 100-200 | Leaflet initialization, tile layers |
| Marker management | 200-400 | Node markers, styles by role |
| Route rendering | 400-600 | Live routes with animations |
| History tool | 600-800 | 24h route history visualization |
| LOS tool | 800-1100 | Line of sight with elevation profile |
| Peers tool | 1100-1300 | Inbound/outbound neighbor analysis |
| Propagation tool | 1300-2000 | RF coverage simulation |
| WebSocket | 2000-2200 | Real-time updates |
| UI handlers | 2200-4100 | Toggle buttons, sliders, search |

Route rendering notes:
- In dev mode (`PROD_MODE=false`), route lines are clickable and log hop-by-hop debug details to the browser console (PR #14).

### styles.css (Styling)

CSS organized by component:
- `.hud` - Main control panel
- `.legend` - Map legend
- `.los-panel`, `.history-panel`, `.peers-panel` - Tool panels
- `.prop-panel` - Propagation settings
- Responsive breakpoints at 900px

### index.html (Template)

HTML shell with `{{PLACEHOLDER}}` syntax for server-side injection:
- `{{SITE_TITLE}}`, `{{SITE_DESCRIPTION}}` - Metadata
- `{{MAP_START_LAT}}`, `{{MAP_START_LON}}` - Initial view
- `{{PROD_MODE}}`, `{{PROD_TOKEN}}` - Auth settings
- `{{TURNSTILE_ENABLED}}`, `{{TURNSTILE_SITE_KEY}}` - Turnstile settings
  (Turnstile is only active when `PROD_MODE=true`)

---

## Data Flow Details

### MQTT Message Processing

```
MQTT Message
    │
    ▼
mqtt_on_message()
    │
    ├── Update topic_counts, stats
    ├── Mark device as seen (mqtt_seen)
    │
    ▼
_try_parse_payload()
    │
    ├── Try JSON coordinate extraction
    ├── Try text pattern matching
    ├── Try MeshCore hex decoding
    │
    ▼
update_queue.put()
    │
    ▼
broadcaster()
    │
    ├── Process device updates
    ├── Process route updates
    ├── Record history
    │
    ▼
WebSocket broadcast to all clients
```

### WebSocket Protocol

**Client receives:**
```javascript
// Initial snapshot
{ type: "snapshot", devices: {...}, trails: {...}, routes: [...], heat: [...] }

// Device update
{ type: "update", device: {...}, trail: [...] }

// Route update
{ type: "route", route: {...} }

// Device seen (online status)
{ type: "device_seen", device_id: "...", mqtt_seen_ts: 1234567890 }

// Stale device removal
{ type: "stale", device_ids: ["..."] }

// History edge update
{ type: "history_edges", edges: [...] }
```

---

## API Endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /` | No | HTML page with injected config |
| `GET /snapshot` | Token | Full state dump |
| `GET /stats` | No | Message counters |
| `GET /api/nodes` | Token | Node list (flat or nested) |
| `GET /peers/{id}` | Token | Inbound/outbound neighbors |
| `GET /preview.png` | No | Social preview image (map tiles + device dots) |
| `GET /los` | No | Line of sight calculation |
| `GET /coverage` | Token | Coverage data proxy |
| `GET /debug/last` | Dev only | Recent MQTT messages |
| `GET /debug/status` | Dev only | Status messages |
| `WS /ws` | Token | Real-time updates |

---

## Configuration Flow

```
.env file
    │
    ▼
docker-compose.yaml (environment:)
    │
    ▼
config.py (os.getenv())
    │
    ├── Backend uses directly
    │
    ▼
app.py root() handler
    │
    ├── Injects into index.html
    │
    ▼
app.js (document.body.dataset)
    │
    └── Frontend uses for initialization
```

---

## Key Design Decisions

### Why no build step?
The project intentionally avoids webpack/bundlers to keep deployment simple. Contributors can edit files and see changes immediately after `docker compose up --build`.

### Why global state?
The original design prioritized simplicity over testability. Future refactoring should inject state as dependencies.

### Why inline JavaScript in Python? (Legacy)
The Node.js decoder script was originally generated at runtime. It's now extracted to `scripts/meshcore_decode.mjs` for maintainability.

### Why 24h route history?
Balances disk usage with useful analytics. Configurable via `ROUTE_HISTORY_HOURS`.

---

## Future Refactoring Targets

### Backend
- [ ] Split `app.py` into route modules (`routes/api.py`, `routes/websocket.py`)
- [ ] Extract MQTT handling to `services/mqtt.py`
- [ ] Extract broadcaster/reaper to `services/`
- [ ] Add pytest tests for decoder, history, LOS

### Frontend
- [ ] Split `app.js` into ES modules
- [ ] Extract tool logic (LOS, history, peers, propagation)
- [ ] Create `storage.js` helper for localStorage patterns
- [ ] Consider TypeScript migration (start with JSDoc types)

---

## Running Locally

```bash
# Copy and configure
cp .env.example .env
# Edit .env with your MQTT settings

# Build and run
docker compose up -d --build

# Check logs
docker compose logs -f meshmap-live

# Verify
curl -s http://localhost:8080/stats
```

---

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run linter
ruff check backend/

# Run tests (when added)
pytest tests/

# Lint JavaScript
npx eslint backend/static/app.js
```

Versioning:
- See `VERSIONS.md` for the changelog; `VERSION.txt` mirrors the latest entry (`1.2.0`).
