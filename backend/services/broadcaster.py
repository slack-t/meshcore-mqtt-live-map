"""
WebSocket broadcaster service.

Processes events from the update queue and broadcasts to connected clients.
"""

import asyncio
import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Set

import state
from config import (
  GIT_CHECK_ENABLED,
  GIT_CHECK_FETCH,
  GIT_CHECK_INTERVAL_SECONDS,
  GIT_CHECK_PATH,
  MAP_RADIUS_KM,
  ROUTE_TTL_SECONDS,
  TRAIL_LEN,
)
from decoder import (
  ROUTE_PAYLOAD_TYPES_SET,
  _append_heat_points,
  _coords_are_zero,
  _rebuild_node_hash_map,
  _route_points_from_device_ids,
  _route_points_from_hashes,
)
from helpers import device_payload, history_edge_payload, route_payload, within_map_radius
from history import _record_route_history
from state import (
  DeviceState,
  device_names,
  device_roles,
  devices,
  mqtt_seen,
  routes,
  seen_devices,
  trails,
)

# Update queue for async processing
update_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

# Git update check state
git_update_info = {
  "available": False,
  "local": None,
  "remote": None,
  "local_short": None,
  "remote_short": None,
  "error": None,
}


def check_git_updates() -> None:
  """Check if there are updates available from upstream."""
  if not GIT_CHECK_ENABLED:
    return

  if not GIT_CHECK_PATH or not os.path.isdir(GIT_CHECK_PATH):
    git_update_info["error"] = "git_path_missing"
    return

  def run_git(args: List[str]) -> str:
    result = subprocess.run(
      args,
      check=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      text=True,
    )
    return result.stdout.strip()

  try:
    subprocess.run(
      ["git", "config", "--global", "--add", "safe.directory", GIT_CHECK_PATH],
      check=False,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
    )
    inside = run_git(["git", "-C", GIT_CHECK_PATH, "rev-parse", "--is-inside-work-tree"])
    if inside.lower() != "true":
      git_update_info["error"] = "not_git_repo"
      return
  except Exception:
    git_update_info["error"] = "git_unavailable"
    return

  try:
    if GIT_CHECK_FETCH:
      subprocess.run(
        ["git", "-C", GIT_CHECK_PATH, "fetch", "--quiet", "--prune"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
      )

    local_sha = run_git(["git", "-C", GIT_CHECK_PATH, "rev-parse", "HEAD"])
    remote_sha = run_git(["git", "-C", GIT_CHECK_PATH, "rev-parse", "@{u}"])
    git_update_info["local"] = local_sha
    git_update_info["remote"] = remote_sha
    git_update_info["local_short"] = local_sha[:7]
    git_update_info["remote_short"] = remote_sha[:7]
    git_update_info["available"] = local_sha != remote_sha
    if git_update_info["available"]:
      print(f"[update] available {git_update_info['local_short']} -> {git_update_info['remote_short']}")
  except Exception:
    git_update_info["error"] = "git_compare_failed"


async def git_check_loop() -> None:
  """Periodically check for git updates."""
  if not GIT_CHECK_ENABLED:
    return
  if GIT_CHECK_INTERVAL_SECONDS <= 0:
    return
  while True:
    await asyncio.sleep(GIT_CHECK_INTERVAL_SECONDS)
    check_git_updates()


def evict_device(device_id: str) -> bool:
  """Remove a device from all state structures."""
  from state import last_seen_broadcast

  removed = False
  if device_id in devices:
    devices.pop(device_id, None)
    removed = True
  trails.pop(device_id, None)
  seen_devices.pop(device_id, None)
  mqtt_seen.pop(device_id, None)
  last_seen_broadcast.pop(device_id, None)
  if removed:
    state.state_dirty = True
    _rebuild_node_hash_map()
  return removed


async def broadcaster() -> None:
  """
  Main broadcaster loop.

  Processes events from the update queue and broadcasts to all connected WebSocket clients.
  """
  from routes.websocket import get_clients

  while True:
    event = await update_queue.get()
    clients = get_clients()

    # Handle device name/role updates
    if isinstance(event, dict) and event.get("type") in ("device_name", "device_role"):
      device_id = event.get("device_id")
      device_state = devices.get(device_id)
      if device_state:
        if device_id in device_names:
          device_state.name = device_names[device_id]
        if device_id in device_roles:
          device_state.role = device_roles[device_id]
        payload = {
          "type": "update",
          "device": device_payload(device_id, device_state),
          "trail": trails.get(device_id, []),
        }
        dead = []
        for ws in list(clients):
          try:
            await ws.send_text(json.dumps(payload))
          except Exception:
            dead.append(ws)
        for ws in dead:
          clients.discard(ws)
      continue

    # Handle device seen (online status) updates
    if isinstance(event, dict) and event.get("type") == "device_seen":
      device_id = event.get("device_id")
      device_state = devices.get(device_id)
      if device_state:
        seen_ts = event.get("last_seen_ts") or time.time()
        mqtt_ts = event.get("mqtt_seen_ts")
        seen_devices[device_id] = seen_ts
        if mqtt_ts:
          mqtt_seen[device_id] = mqtt_ts
        payload = {
          "type": "device_seen",
          "device_id": device_id,
          "last_seen_ts": seen_ts,
          "mqtt_seen_ts": mqtt_ts,
        }
        dead = []
        for ws in list(clients):
          try:
            await ws.send_text(json.dumps(payload))
          except Exception:
            dead.append(ws)
        for ws in dead:
          clients.discard(ws)
      continue

    # Handle device removal
    if isinstance(event, dict) and event.get("type") == "device_remove":
      device_id = event.get("device_id")
      if device_id and evict_device(device_id):
        payload = {"type": "stale", "device_ids": [device_id]}
        dead = []
        for ws in list(clients):
          try:
            await ws.send_text(json.dumps(payload))
          except Exception:
            dead.append(ws)
        for ws in dead:
          clients.discard(ws)
      continue

    # Handle route updates
    if isinstance(event, dict) and event.get("type") == "route":
      route_mode = event.get("route_mode")
      points = event.get("points")
      used_hashes: List[str] = []
      point_ids: List[Optional[str]] = []

      if not points:
        path_hashes = event.get("path_hashes") or []
        points, used_hashes, point_ids = _route_points_from_hashes(
          list(path_hashes),
          event.get("origin_id"),
          event.get("receiver_id"),
          event.get("ts") or time.time(),
        )

      if not points and route_mode == "fanout":
        points = _route_points_from_device_ids(event.get("origin_id"), event.get("receiver_id"))
        if points and event.get("origin_id") and event.get("receiver_id") and len(points) == 2:
          point_ids = [event.get("origin_id"), event.get("receiver_id")]

      # Fallback: draw direct link if path hashes missing
      if not points:
        points = _route_points_from_device_ids(event.get("origin_id"), event.get("receiver_id"))
        if points:
          route_mode = "direct"
          if event.get("origin_id") and event.get("receiver_id") and len(points) == 2:
            point_ids = [event.get("origin_id"), event.get("receiver_id")]

      if not points:
        continue

      # Filter routes outside map radius
      if MAP_RADIUS_KM > 0:
        outside = any(
          not within_map_radius(point[0], point[1])
          for point in points
          if isinstance(point, (list, tuple)) and len(point) >= 2
        )
        if outside:
          continue

      route_id = (
        event.get("route_id")
        or event.get("message_hash")
        or f"{event.get('origin_id', 'route')}-{int(event.get('ts', time.time()) * 1000)}"
      )
      expires_at = (event.get("ts") or time.time()) + ROUTE_TTL_SECONDS
      route = {
        "id": route_id,
        "points": points,
        "hashes": used_hashes,
        "point_ids": point_ids,
        "route_mode": route_mode or ("path" if used_hashes else "direct"),
        "ts": event.get("ts") or time.time(),
        "expires_at": expires_at,
        "origin_id": event.get("origin_id"),
        "receiver_id": event.get("receiver_id"),
        "payload_type": event.get("payload_type"),
        "message_hash": event.get("message_hash"),
        "snr_values": event.get("snr_values"),
        "topic": event.get("topic"),
      }
      _append_heat_points(points, route["ts"], event.get("payload_type"))
      routes[route_id] = route

      history_updates, history_removed = _record_route_history(route)

      payload = {"type": "route", "route": route_payload(route)}
      dead = []
      for ws in list(clients):
        try:
          await ws.send_text(json.dumps(payload))
        except Exception:
          dead.append(ws)
      for ws in dead:
        clients.discard(ws)

      # Broadcast history updates
      if history_updates or history_removed:
        history_payload = {}
        if history_updates:
          history_payload["type"] = "history_edges"
          history_payload["edges"] = [history_edge_payload(edge) for edge in history_updates]
        if history_removed:
          history_payload_remove = {"type": "history_edges_remove", "edge_ids": history_removed}
        else:
          history_payload_remove = None
        dead = []
        for ws in list(clients):
          try:
            if history_updates:
              await ws.send_text(json.dumps(history_payload))
            if history_payload_remove:
              await ws.send_text(json.dumps(history_payload_remove))
          except Exception:
            dead.append(ws)
        for ws in dead:
          clients.discard(ws)
      continue

    # Handle device position updates
    upd = event.get("data") if isinstance(event, dict) and event.get("type") == "device" else event

    device_id = upd["device_id"]
    if not within_map_radius(upd.get("lat"), upd.get("lon")):
      if evict_device(device_id):
        payload = {"type": "stale", "device_ids": [device_id]}
        dead = []
        for ws in list(clients):
          try:
            await ws.send_text(json.dumps(payload))
          except Exception:
            dead.append(ws)
        for ws in dead:
          clients.discard(ws)
      continue

    is_new_device = device_id not in devices
    device_state = DeviceState(
      device_id=device_id,
      lat=upd["lat"],
      lon=upd["lon"],
      ts=upd.get("ts", time.time()),
      heading=upd.get("heading"),
      speed=upd.get("speed"),
      rssi=upd.get("rssi"),
      snr=upd.get("snr"),
      name=upd.get("name") or device_names.get(device_id),
      role=upd.get("role") or device_roles.get(device_id),
      raw_topic=upd.get("raw_topic"),
    )
    devices[device_id] = device_state
    seen_devices[device_id] = time.time()
    state.state_dirty = True

    if is_new_device:
      _rebuild_node_hash_map()

    if device_state.name:
      device_names[device_id] = device_state.name
    if device_state.role:
      device_roles[device_id] = device_state.role

    # Update trail
    if TRAIL_LEN > 0 and not _coords_are_zero(device_state.lat, device_state.lon):
      trails.setdefault(device_id, [])
      trails[device_id].append([device_state.lat, device_state.lon, device_state.ts])
      if len(trails[device_id]) > TRAIL_LEN:
        trails[device_id] = trails[device_id][-TRAIL_LEN:]
    elif device_id in trails:
      trails.pop(device_id, None)

    payload = {
      "type": "update",
      "device": device_payload(device_id, device_state),
      "trail": trails.get(device_id, []),
    }

    dead = []
    for ws in list(clients):
      try:
        await ws.send_text(json.dumps(payload))
      except Exception:
        dead.append(ws)
    for ws in dead:
      clients.discard(ws)
