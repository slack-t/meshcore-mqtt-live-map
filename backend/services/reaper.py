"""
Reaper service for cleaning up stale devices, routes, and history.
"""

import asyncio
import json
import time

import state
from config import (
  DEVICE_TTL_SECONDS,
  HEAT_TTL_SECONDS,
  MESSAGE_ORIGIN_TTL_SECONDS,
)
from decoder import _coords_are_zero, _rebuild_node_hash_map
from history import _prune_route_history
from state import (
  devices,
  heat_events,
  message_origins,
  routes,
  seen_devices,
  trails,
)


async def reaper() -> None:
  """
  Periodically clean up stale data.

  - Removes devices that haven't been seen within DEVICE_TTL_SECONDS
  - Removes expired routes
  - Prunes heat events
  - Cleans up message origin cache
  """
  from routes.websocket import get_clients

  while True:
    now = time.time()
    clients = get_clients()

    # Clean up stale devices
    if DEVICE_TTL_SECONDS > 0:
      stale = [dev_id for dev_id, st in list(devices.items()) if now - st.ts > DEVICE_TTL_SECONDS]
      if stale:
        payload = {"type": "stale", "device_ids": stale}
        dead = []
        for ws in list(clients):
          try:
            await ws.send_text(json.dumps(payload))
          except Exception:
            dead.append(ws)
        for ws in dead:
          clients.discard(ws)

        for dev_id in stale:
          devices.pop(dev_id, None)
          trails.pop(dev_id, None)
          state.state_dirty = True
        _rebuild_node_hash_map()

    # Clean up routes with zero coordinates
    if routes:
      bad_routes = []
      for route_id, route in list(routes.items()):
        points = route.get("points") if isinstance(route, dict) else None
        if not isinstance(points, list):
          continue
        if any(_coords_are_zero(p[0], p[1]) for p in points if isinstance(p, list) and len(p) >= 2):
          bad_routes.append(route_id)
      if bad_routes:
        payload = {"type": "route_remove", "route_ids": bad_routes}
        dead = []
        for ws in list(clients):
          try:
            await ws.send_text(json.dumps(payload))
          except Exception:
            dead.append(ws)
        for ws in dead:
          clients.discard(ws)
        for route_id in bad_routes:
          routes.pop(route_id, None)

    # Clean up expired routes
    stale_routes = [route_id for route_id, route in list(routes.items()) if now > route.get("expires_at", 0)]
    if stale_routes:
      payload = {"type": "route_remove", "route_ids": stale_routes}
      dead = []
      for ws in list(clients):
        try:
          await ws.send_text(json.dumps(payload))
        except Exception:
          dead.append(ws)
      for ws in dead:
        clients.discard(ws)
      for route_id in stale_routes:
        routes.pop(route_id, None)

    # Prune route history
    history_updates, history_removed = _prune_route_history()
    if history_updates or history_removed:
      dead = []
      for ws in list(clients):
        try:
          if history_updates:
            await ws.send_text(json.dumps({"type": "history_edges", "edges": history_updates}))
          if history_removed:
            await ws.send_text(json.dumps({"type": "history_edges_remove", "edge_ids": history_removed}))
        except Exception:
          dead.append(ws)
      for ws in dead:
        clients.discard(ws)

    # Clean up old heat events
    if HEAT_TTL_SECONDS > 0 and heat_events:
      cutoff = now - HEAT_TTL_SECONDS
      heat_events[:] = [entry for entry in heat_events if entry.get("ts", 0) >= cutoff]

    # Clean up message origin cache
    if message_origins:
      for msg_hash, info in list(message_origins.items()):
        if now - info.get("ts", 0) > MESSAGE_ORIGIN_TTL_SECONDS:
          message_origins.pop(msg_hash, None)

    # Clean up old seen_devices entries
    prune_after = max(DEVICE_TTL_SECONDS * 3, 900) if DEVICE_TTL_SECONDS > 0 else 86400
    for dev_id, last in list(seen_devices.items()):
      if now - last > prune_after:
        seen_devices.pop(dev_id, None)

    await asyncio.sleep(5)
