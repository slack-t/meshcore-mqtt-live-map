"""
State persistence service.

Handles loading and saving of device state, trails, and role overrides.
"""

import asyncio
import json
import os
from dataclasses import asdict
from typing import Any, Dict, Set

import state
from config import (
  DEVICE_ROLES_FILE,
  STATE_DIR,
  STATE_FILE,
  STATE_SAVE_INTERVAL,
  TRAIL_LEN,
)
from decoder import _coords_are_zero, _normalize_role, _rebuild_node_hash_map
from helpers import within_map_radius
from state import (
  DeviceState,
  device_names,
  device_role_sources,
  device_roles,
  devices,
  seen_devices,
  trails,
)


def load_role_overrides() -> Dict[str, str]:
  """Load device role overrides from file."""
  if not DEVICE_ROLES_FILE or not os.path.exists(DEVICE_ROLES_FILE):
    return {}
  try:
    with open(DEVICE_ROLES_FILE, "r", encoding="utf-8") as handle:
      data = json.load(handle)
  except Exception:
    return {}
  if not isinstance(data, dict):
    return {}
  roles: Dict[str, str] = {}
  for key, value in data.items():
    if not isinstance(key, str) or not isinstance(value, str):
      continue
    role = _normalize_role(value)
    if not role:
      continue
    roles[key.strip()] = role
  return roles


def serialize_state() -> Dict[str, Any]:
  """Serialize current state for saving."""
  return {
    "version": 1,
    "saved_at": __import__("time").time(),
    "devices": {k: asdict(v) for k, v in devices.items()},
    "trails": trails,
    "seen_devices": seen_devices,
    "device_names": device_names,
    "device_roles": device_roles,
    "device_role_sources": device_role_sources,
  }


def load_state() -> None:
  """Load state from file."""
  try:
    if not os.path.exists(STATE_FILE):
      return
    with open(STATE_FILE, "r", encoding="utf-8") as handle:
      data = json.load(handle)
  except Exception as exc:
    print(f"[state] failed to load {STATE_FILE}: {exc}")
    return

  # Load devices
  raw_devices = data.get("devices") or {}
  loaded_devices: Dict[str, DeviceState] = {}
  dropped_ids: Set[str] = set()
  for key, value in raw_devices.items():
    if not isinstance(value, dict):
      continue
    try:
      device_state = DeviceState(**value)
    except Exception:
      continue
    if _coords_are_zero(device_state.lat, device_state.lon) or not within_map_radius(device_state.lat, device_state.lon):
      dropped_ids.add(str(key))
      continue
    loaded_devices[key] = device_state

  devices.clear()
  devices.update(loaded_devices)

  # Load trails
  trails.clear()
  trails.update(data.get("trails") or {})
  seen_devices.clear()
  seen_devices.update(data.get("seen_devices") or {})

  # Clean trails
  cleaned_trails: Dict[str, list] = {}
  trails_dirty = False
  for device_id, trail in trails.items():
    if not isinstance(trail, list):
      continue
    filtered: list = []
    for entry in trail:
      if not isinstance(entry, (list, tuple)) or len(entry) < 2:
        continue
      lat = entry[0]
      lon = entry[1]
      try:
        lat_val = float(lat)
        lon_val = float(lon)
      except (TypeError, ValueError):
        continue
      if _coords_are_zero(lat_val, lon_val) or not within_map_radius(lat_val, lon_val):
        trails_dirty = True
        continue
      filtered.append(list(entry))
    if filtered:
      cleaned_trails[device_id] = filtered
    else:
      trails_dirty = True

  trails.clear()
  trails.update(cleaned_trails)

  # Disable trails if TRAIL_LEN <= 0
  if TRAIL_LEN <= 0 and trails:
    trails.clear()
    trails_dirty = True

  # Clean up dropped devices
  if dropped_ids:
    for device_id in dropped_ids:
      trails.pop(device_id, None)
      seen_devices.pop(device_id, None)
      trails_dirty = True

  if trails_dirty:
    state.state_dirty = True

  # Load device names
  raw_names = data.get("device_names") or {}
  if isinstance(raw_names, dict):
    device_names.clear()
    device_names.update({str(k): str(v) for k, v in raw_names.items() if str(v).strip()})
  else:
    device_names.clear()
  if dropped_ids:
    for device_id in dropped_ids:
      device_names.pop(device_id, None)

  # Load role sources
  raw_role_sources = data.get("device_role_sources") or {}
  if isinstance(raw_role_sources, dict):
    device_role_sources.clear()
    device_role_sources.update({str(k): str(v) for k, v in raw_role_sources.items() if str(v).strip()})
  else:
    device_role_sources.clear()
  if dropped_ids:
    for device_id in dropped_ids:
      device_role_sources.pop(device_id, None)

  # Load device roles
  raw_roles = data.get("device_roles") or {}
  device_roles.clear()
  if isinstance(raw_roles, dict):
    for key, value in raw_roles.items():
      if dropped_ids and str(key) in dropped_ids:
        continue
      role_value = str(value).strip() if isinstance(value, str) else ""
      if not role_value:
        continue
      source = device_role_sources.get(str(key))
      if source in ("explicit", "override"):
        device_roles[str(key)] = role_value

  # Apply role overrides
  role_overrides = load_role_overrides()
  if role_overrides:
    for device_id in role_overrides:
      device_role_sources[device_id] = "override"
    device_roles.update(role_overrides)
  if dropped_ids:
    for device_id in dropped_ids:
      device_roles.pop(device_id, None)

  # Rebuild hash map
  _rebuild_node_hash_map()

  # Apply names and roles to device states
  for device_id, device_state in devices.items():
    if not device_state.name and device_id in device_names:
      device_state.name = device_names[device_id]
    role_value = device_roles.get(device_id)
    device_state.role = role_value if role_value else None


async def state_saver() -> None:
  """Periodically save state to file."""
  while True:
    if state.state_dirty:
      try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp_path = f"{STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
          json.dump(serialize_state(), handle)
        os.replace(tmp_path, STATE_FILE)
        state.state_dirty = False
      except Exception as exc:
        print(f"[state] failed to save {STATE_FILE}: {exc}")
    await asyncio.sleep(max(1.0, STATE_SAVE_INTERVAL))
