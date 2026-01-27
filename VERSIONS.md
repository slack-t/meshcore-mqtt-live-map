# Versions

## v1.2.0 (01-27-2026)
- Add Cloudflare Turnstile protection with a landing/verification flow and auth cookie.
- Turnstile now only activates when `PROD_MODE=true` (even if `TURNSTILE_ENABLED=true`).
- Preserve Discord/social embeds by allowlisting common bots via user-agent bypass.
- Hide the Turnstile site key from the page while still providing it to the widget.
- Credit: Nasticator (PR #13).
- New envs:
  - `TURNSTILE_ENABLED`
  - `TURNSTILE_SITE_KEY`
  - `TURNSTILE_SECRET_KEY`
  - `TURNSTILE_API_URL`
  - `TURNSTILE_TOKEN_TTL_SECONDS`
  - `TURNSTILE_BOT_BYPASS`
  - `TURNSTILE_BOT_ALLOWLIST`

## v1.1.2 (01-27-2026)
- Dev route debug: route lines are now clickable (when `PROD_MODE=false`) and log rich hop-by-hop details to the browser console (distance, hops, hashes, origin/receiver, timestamps). Credit: https://github.com/sefator (PR #14).

## v1.1.1 (01-26-2026)
- Fix: First-hop route selection now prefers the closest repeater/room to the origin when short-hash collisions occur, preventing cross-city mis-picks (Issue: https://github.com/yellowcooln/meshcore-mqtt-live-map/issues/11).

## v1.1.0 (01-21-2026)
- History panel can be dismissed with an X while keeping history lines visible (re-open via History tool).
- Bump service worker cache and asset version to ensure the new History panel behavior loads.

## v1.0.9 (01-16-2026)
- Enforce `ROUTE_MAX_HOP_DISTANCE` for fallback-selected hops to prevent unrealistic jumps (credit: https://github.com/sefator).

## v1.0.8 (01-14-2026)
- Enforce `ROUTE_MAX_HOP_DISTANCE` across fallback hops, direct routes, and receiver appends to prevent cross-region path jumps.

## v1.0.7 (01-14-2026)
- Route hash collisions now prefer known neighbor pairs before falling back to closest-hop selection.
- Add optional neighbor override map via `NEIGHBOR_OVERRIDES_FILE` (default `data/neighbor_overrides.json`).
- Neighbor edges auto-expire using `DEVICE_TTL_SECONDS` to prevent stale adjacency picks.

## v1.0.6 (01-13-2026)
- Peers panel now labels line colors (blue = incoming, purple = outgoing).
- Propagation origins can be removed individually by clicking their markers.
- HUD scrollbars styled in Chromium for a cleaner look.
- Bump PWA cache version to force asset refresh.
- Suggestions from Zaos.

## v1.0.5 (01-13-2026)
- Resolve short-hash collisions by choosing the closest node in the route chain (credit: https://github.com/sefator)
- Drop hops that exceed `ROUTE_MAX_HOP_DISTANCE` to avoid unrealistic jumps
- Add `ROUTE_INFRA_ONLY` to restrict route lines to repeaters/rooms
- Document new route env defaults in `.env.example`

## v1.0.4 (01-13-2026)
- Open Graph preview URL no longer double-slashes the `/preview.png` path (credit: https://github.com/chrisdavis2110)
- Preview image now renders in-bounds device dots (not just the center pin; credit: https://github.com/chrisdavis2110)
- Fix preview renderer NameError by importing `Tuple`

## v1.0.3 (01-12-2026)
- Fix route decoding to return the correct tuple when paths exceed max length (credit: https://github.com/sefator)

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
