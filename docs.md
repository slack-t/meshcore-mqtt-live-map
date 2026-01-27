# Mesh Map Live: Implementation Notes

This document captures the state of the project and the key changes made so far, so a new Codex session can pick up without losing context.
Current version: `1.2.0` (see `VERSIONS.md`).

## Overview
This project renders live MeshCore traffic on a Leaflet + OpenStreetMap map. A FastAPI backend subscribes to MQTT (WSS/TLS or TCP), decodes MeshCore packets using `@michaelhart/meshcore-decoder`, and broadcasts device updates and routes over WebSockets to the frontend. Core logic is split into config/state/decoder/LOS/history modules so changes are localized. The UI includes heatmap, LOS tools, map mode toggles, and a 24‑hour route history layer.

## Versioning
- `VERSION.txt` holds the current version string (`1.2.0`).
- `VERSIONS.md` is an append-only changelog by version.

## Key Paths
- `backend/app.py`: FastAPI server + MQTT lifecycle and websocket broadcasting.
- `backend/config.py`: environment/config constants (shared across backend modules).
- `backend/state.py`: shared runtime state (devices/routes/history) + dataclasses.
- `backend/decoder.py`: payload parsing, meshcore-decoder integration, route helpers.
- `backend/los.py`: LOS math + elevation sampling.
- `backend/history.py`: route history persistence + pruning.
- `backend/static/index.html`: HTML shell + template placeholders.
- `backend/static/styles.css`: UI styles.
- `backend/static/app.js`: Leaflet UI, markers, legends, routes, tools.
- `backend/static/sw.js`: PWA service worker.
- `docker-compose.yaml`: runtime configuration.
- `data/state.json`: persisted device/trail/roles/names (loaded at startup).
- `data/route_history.jsonl`: rolling 24h route history segments (lines).
- `data/neighbor_overrides.json`: optional neighbor override pairs for route disambiguation.
- `.env`: dev configuration (mirrors template variables).

## Runtime Commands (Typical Workflow)
- `docker compose up -d --build` (run after any file changes).
- `docker compose logs -f meshmap-live` (watch MQTT + decode logs).
- `curl -s http://localhost:8080/snapshot` (current device map).
- `curl -s http://localhost:8080/stats` (counters, route types).
- `curl -s http://localhost:8080/debug/last` (recent MQTT decode/debug entries).
- `curl -s http://localhost:8080/peers/<device_id>` (peer counts for a node; uses route history).

## Env Notes (Recent Additions)
- `CUSTOM_LINK_URL` adds a HUD link button; blank hides it.
- `MQTT_ONLINE_FORCE_NAMES` forces named nodes to show MQTT online and skips them in peers.
- `GIT_CHECK_ENABLED`, `GIT_CHECK_FETCH`, `GIT_CHECK_PATH` enable update checks.
- `GIT_CHECK_INTERVAL_SECONDS` controls how often the server re-checks for updates.
- `ROUTE_MAX_HOP_DISTANCE` prunes hops longer than the configured km distance.
- `ROUTE_INFRA_ONLY` limits route lines to repeaters/rooms (companions excluded from routes).
- `NEIGHBOR_OVERRIDES_FILE` points at an optional JSON file with neighbor pairs to resolve hash collisions.
- Turnstile protection is gated by `PROD_MODE=true` and controlled by:
  `TURNSTILE_ENABLED`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`,
  `TURNSTILE_API_URL`, and `TURNSTILE_TOKEN_TTL_SECONDS`.
- Discord/social embeds can be preserved under Turnstile with
  `TURNSTILE_BOT_BYPASS` and `TURNSTILE_BOT_ALLOWLIST`.

## MQTT + Decoder
- MQTT supports **WebSockets + TLS** or plain TCP. Typical deployments use `MQTT_TRANSPORT=websockets`, `MQTT_TLS=true`, and `MQTT_WS_PATH=/` or `/mqtt`.
- Decoder uses Node + `@michaelhart/meshcore-decoder` installed in the container.
- `backend/decoder.py` writes a small Node helper and calls it to decode MeshCore packets.

## Frontend UI
- Header includes a GitHub link icon and HUD summary (stats, feed note).
- Base map toggle: Light/Dark/Topo; persisted to localStorage.
- Dark map also darkens node popups for readability.
- Node popups do not auto-pan; dragging the map won’t snap back to keep a popup in view.
- Legend is collapsible and persisted to localStorage.
- HUD is capped to `90vh` and scrolls to avoid running off-screen.
- Map start position is configurable with `MAP_START_LAT`, `MAP_START_LON`, `MAP_START_ZOOM`.
- Radius filter: `MAP_RADIUS_KM=0` disables filtering; `.env.example` uses `241.4` km (150mi). `MAP_RADIUS_SHOW=true` draws a debug circle.
- Default base layer can be set with `MAP_DEFAULT_LAYER` (localStorage overrides).
- Units toggle (km/mi) is site-wide; default from `DISTANCE_UNITS` and stored in localStorage.
- Node size slider defaults from `NODE_MARKER_RADIUS` and persists in localStorage.
- History link size slider defaults from `HISTORY_LINK_SCALE` and persists in localStorage.
- Node search (name or key) and a labels toggle (persisted to localStorage).
- History tool defaults off and opens a right-side panel with a heat filter slider (visibility is not persisted).
- History panel can be dismissed with the X button while keeping history lines visible (toggle History tool to show it again).
- History slider modes: 0 = All, 1 = Blue only, 2 = Yellow only, 3 = Yellow + Red, 4 = Red only.
- History legend swatch is hidden unless the History tool is active.
- Peers tool shows incoming/outgoing neighbors for a selected node, with counts and percentages pulled from route history.
- Peers tool skips nodes listed in `MQTT_ONLINE_FORCE_NAMES` (observer listeners).
- Peers panel legend clarifies line colors (incoming = blue, outgoing = purple).
- Coverage tool only appears when `COVERAGE_API_URL` is set; it fetches tiles on demand.
- Trail text in the HUD is only shown when `TRAIL_LEN > 0`; `TRAIL_LEN=0` disables trails entirely.
- Hide Nodes toggle hides markers, trails, heat, routes, and history layers.
- Heat toggle can hide the heatmap; it defaults on and the button turns green when heat is off.
- HUD logo uses `SITE_ICON`; if unset or broken it falls back to a small “Map” badge so the toggle still works.
- History line weight was reduced for improved readability.
- Propagation overlay keeps heat/routes/trails/markers above it after render; the panel lives on the right and retains the last render until you generate a new one.
- Propagation origin markers can be removed individually by clicking them.
- Heatmap includes all route payload types (adverts are no longer skipped).
- MQTT online status shows as a green marker outline and popup status; it uses `mqtt_seen_ts` from `/status` or `/packets` topics (configurable).
- `MQTT_ONLINE_FORCE_NAMES` can force named nodes to show as MQTT online regardless of last seen.
- PWA install support is enabled via `/manifest.webmanifest` and a service worker at `/sw.js`.
- Preview image (`/preview.png`) renders in-bounds device dots for shared links.
- Route lines prefer known neighbor pairs (including overrides) before falling back to closest-hop selection.
- Clicking the HUD logo hides/shows the left panel while tool panels stay open.
- Share button copies a URL with the current view + toggles (including HUD visibility).
- Optional custom HUD link appears when `CUSTOM_LINK_URL` is set.
- Update banner shows when `GIT_CHECK_ENABLED=true` and the repo is behind; users can dismiss it per remote SHA.
- Update banner dismissal relies on `.hud-update[hidden]` to ensure the banner actually disappears.
- URL params override stored settings: `lat`, `lon`/`lng`/`long`, `zoom`, `layer`, `history`, `heat`, `labels`, `nodes`, `legend`, `menu`, `units`, `history_filter`.
- Service worker uses `no-store` for navigation requests so env-driven UI toggles (like the radius ring) update without clearing site data.
- HUD scrollbars are custom styled in Chromium for a cleaner look.

## LOS (Line of Sight) Tool
- LOS runs **server-side only** via `/los` (no client-side elevation fetch).
- UI draws an LOS line (green clear / red blocked), renders an elevation profile, and marks peaks.
- When blocked, the server can return a relay suggestion marker (amber/green).
- Peak markers show coords + elevation and copy coords on click.
- Hovering the profile or the LOS line syncs a cursor tooltip on the profile.
- Hovering the LOS profile also tracks a cursor on the map and highlights nearby peaks.
- LOS legend items (clear/blocked/peaks/relay) are hidden unless the LOS tool is active.
- Shift+click nodes (or long‑press on mobile) or click two points on the map to run LOS.

## Device Names + Roles
- Names come from advert payloads or status messages when available.
- Roles are only accepted from explicit decoder fields:
  - `deviceRole`/`deviceRoleName` (MeshCore advert flags), or `role` fields in payload.
  - Name-based role heuristics were removed to avoid mislabels.
- Roles are **not** assigned to the receiver of a packet. For decoded packets, the role applies to the **advertised pubkey** (decoded `location.pubkey` or `decoded_pubkey`).
- Roles persist to `data/state.json` with `device_role_sources`. Only explicit/override roles are restored on load.
- Optional overrides: `data/device_roles.json` can force roles per device_id.

## Routes / Message Paths
Routes are drawn when:
- A packet contains a path list (decoder `pathHashes` or `path`), or
- Multiple observers see the same message hash (fanout), or
- As a fallback, when one hash maps to a known device, a direct line is drawn to the receiver.
When a hop hash collides, the backend prefers neighbor pairs (or overrides) before falling back to closest-hop selection; oversized path lists are ignored via `ROUTE_PATH_MAX_LEN`.
All route modes enforce `ROUTE_MAX_HOP_DISTANCE` for every hop (including direct and receiver appends) to prevent cross‑region jumps.

### 24h History Layer
- Every route segment is persisted to `data/route_history.jsonl` and kept for the last `ROUTE_HISTORY_HOURS`.
- History lines are color‑coded by volume (blue = low, orange = mid, red = high) and weight scales with counts.
- History is hidden by default; the History tool opens a right panel with a slider to filter by heat band.
- The History tool also includes a link size slider; it scales line weight without changing counts.
- History records route modes from `ROUTE_HISTORY_ALLOWED_MODES` (default: `path`).

If routes aren’t visible:
- The packet may only include a single hop (`path: ["24"]`).
- Other repeaters might not be publishing to MQTT, so the message is only seen by one observer.
- Routes and trails drop any `0,0` coordinates (including string values) and will purge bad entries on load.
- Route styling uses payload type: 2/5 = Message (blue), 8/9 = Trace (orange), 4 = Advert (green).
- If history is empty but routes show, confirm `ROUTE_HISTORY_ALLOWED_MODES` includes the active route mode.

## Frontend Map UI
- Legend includes Trace/Message/Advert line styles and Repeater/Companion/Room/Unknown dot colors.
- Unknown markers were made more visible (larger, higher contrast gray).
- Zoom control moved to bottom-right.
- Route lines are thicker/bolder for large screens.
- LOS + Propagation panels appear on the right; on mobile they stack to avoid overlap.

## Persistence
- Devices, trails, names, and roles are saved to `data/state.json`.
- On restart, devices should stay visible if `state.json` exists.
- Route history is persisted separately to `data/route_history.jsonl` (rolling window).
- If stale/mis-labeled roles appear, delete `data/state.json` or remove role entries.
- State load now removes any `0,0` coordinates from devices/trails (including string values).
- When `TRAIL_LEN=0`, stored trails are cleared on load and no new trails are written.

## Troubleshooting Notes
- If map is empty but MQTT is connected, check `/debug/last` for decoded payloads and `payloadType`.
- If markers appear in the wrong place, inspect `decoder_meta` and location fields.
- If roles flip incorrectly, verify `role_target_id` in `/debug/last`.
- If routes don’t show, verify message hashes appear under multiple receivers in MQTT.
- If MQTT online looks wrong, confirm `MQTT_ONLINE_TOPIC_SUFFIXES` in `.env` (default `/status,/packets`).

## Recent Fixes / Changes Summary
- Added full WSS support and TLS options.
- Integrated meshcore-decoder for advert/location + role parsing.
- Added `/stats`, `/snapshot`, `/debug/last`, `/debug/status` endpoints.
- Added persistence and state reload logic; safer role restore rules.
- Added route drawing for traces/paths/messages with TTL cleanup.
- Added fallback route when only one hop is known.
- UI: route legend, role legend, and improved marker styles.
- Roles now apply to advertised pubkey, not receiver.
- Docker restarts are required after file changes (always run `docker compose up -d --build`).
- LOS is server-side only; elevation profile/peaks are returned by `/los`.
- MQTT online indicator (green outline + legend) and configurable online window.
- Filters out `0,0` GPS points from devices, trails, and routes (including string values).
- Added 24h route history storage + history toggle with volume-based colors.
- Hide nodes now hides heat/routes/history along with markers/trails.
- Fixed MQTT disconnect callback signature so broker drops don’t crash the MQTT loop.
- Route hash collisions prefer known neighbors (or overrides) before closest-hop selection; long path lists are skipped (`ROUTE_PATH_MAX_LEN`).
- First-hop hash collisions now prefer the closest node to the origin to avoid cross-city mis-picks (Issue #11).
- Dev-only route debugging: clicking a route line logs hop-by-hop metadata (distances, hashes, origin/receiver) to the browser console when `PROD_MODE=false` (PR #14, credit: https://github.com/sefator).
- Trails can be disabled by setting `TRAIL_LEN=0` (HUD trail text is removed).
- Node marker size can be tuned via `NODE_MARKER_RADIUS` (users can override locally).
- Units toggle defaults from `DISTANCE_UNITS` and persists in localStorage.
- Mobile LOS selection supports long-press on nodes.
- History tool visibility no longer persists (always off unless `history=on` in the URL).
