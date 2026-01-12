# Versions

## v1.0.3 (01-11-2026)
- Modular backend layout with `backend/routes/` and `backend/services/` for clearer separation
- Add `backend/scripts/meshcore_decode.mjs` as the Node decoder helper
- Static files now resolve from `APP_DIR/static` for consistent pathing
- Documentation updated to reflect the new module layout

## v1.0.2 (01-11-2026)
- Fix update banner Hide action by honoring the hidden state in CSS
- Remove update banner debug logging after verification

## v1.0.1 (01-11-2025)
- Update check banner (git local vs upstream) with dismiss + auto recheck every 12 hours
- Custom HUD link button (configurable via env, hidden when unset)
- Update banner rendered from HTML dataset to avoid JS/token fetch issues
- Git repo mounted into container for update checks; safe.directory configured automatically
- Update banner Hide button styled to match HUD controls
- New envs: `CUSTOM_LINK_URL`, `MQTT_ONLINE_FORCE_NAMES`, `GIT_CHECK_ENABLED`, `GIT_CHECK_FETCH`, `GIT_CHECK_PATH`, `GIT_CHECK_INTERVAL_SECONDS`

## v1.0.0 (01-10-2025)
- Live MeshCore node map with MQTT ingest, websocket updates, and Leaflet UI
- Node markers with roles, names, and MQTT online ring
- Trace/path, message, and advert route lines with animations
- Heatmap for recent activity (toggle + intensity slider)
- 24h history tool with heat filter + link weight slider
- Peers tool showing inbound/outbound neighbors with map lines
- LOS tool with elevation profile, peaks, relay suggestion, and mobile support
- Propagation tool with right-side panel and map overlay
- Search, labels toggle, hide nodes, map layer toggles, and shareable URL params
- Distance unit toggle (km/mi) with per-user preference
- PWA install support (manifest + service worker)
- Persistent state + route history on disk
