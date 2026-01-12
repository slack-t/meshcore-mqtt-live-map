# Contributing Guide

Thanks for helping improve the MeshCore Live Map. This repo is intentionally lightweight (no build step), so keep changes simple and readable.

## Quick Start
1) Copy `.env.example` to `.env` and set MQTT details.
2) Rebuild: `docker compose up -d --build`
3) Verify: `curl -s http://localhost:8080/snapshot`

## Versioning
- Append a new section to `VERSIONS.md` describing the change set.
- Update `VERSION.txt` to match the latest entry.

## Project Layout
- `backend/app.py` handles MQTT ingest, decoding, routing, and API endpoints.
- `backend/routes/` holds HTTP/WebSocket route handlers split by concern.
- `backend/services/` holds background services (MQTT, broadcaster, reaper, persistence).
- `backend/scripts/meshcore_decode.mjs` is the Node MeshCore decoder helper.
- `backend/static/index.html` is the HTML shell + template placeholders.
- `backend/static/styles.css` holds all UI styling.
- `backend/static/app.js` contains all client-side map logic (Leaflet, routes, LOS, propagation).
- `backend/static/sw.js` is the PWA service worker.

## Coding Style
- Use 2-space indentation everywhere (Python, HTML, CSS, JS).
- Keep helpers small and focused; prefer new helpers over huge functions.
- Avoid new dependencies unless they’re critical.

## Testing Checklist
- `docker compose up -d --build` after any change.
- `curl -s http://localhost:8080/stats` to confirm MQTT ingest.
- Open the map: confirm markers, LOS, and propagation behave as expected.
- If `COVERAGE_API_URL` is blank, confirm the Coverage button is hidden.
- Note: coordinates at `0,0` (even as strings) are filtered and won’t render.
- Radius filter: `MAP_RADIUS_KM` defaults to 241.4 km (150mi); set `0` to disable.
- `CUSTOM_LINK_URL` shows an extra HUD link when set; leave blank to hide.
- Update banner uses `GIT_CHECK_ENABLED` + `GIT_CHECK_PATH` to compare local vs upstream.

## UI Changes
When adding UI controls:
- Wire the toggle into `app.js`.
- Add styles to `styles.css` (don’t inline).
- Keep HUD layout stable on mobile (test at narrow widths).
- If `SITE_ICON` is optional, include a text fallback for the HUD toggle.
- If you add view state, decide whether it should persist in localStorage or only via URL params (History tool defaults off).
- Node marker size defaults to `NODE_MARKER_RADIUS` and can be overridden by the HUD slider (persisted in localStorage).
- History link size defaults to `HISTORY_LINK_SCALE` and can be overridden in the History panel (persisted in localStorage).
- Peers tool uses `/peers/{device_id}`; keep the response stable when modifying route history logic.

## API Changes
- Document new endpoints in `docs.md`.
- Keep payloads backward-compatible when possible.

## Commits
Use short, imperative commit messages, e.g. `Add LOS panel toggle`.
