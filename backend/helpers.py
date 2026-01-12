"""
Shared helper functions for routes and services.
"""

import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import (
  MAP_RADIUS_KM,
  MAP_START_LAT,
  MAP_START_LON,
  MQTT_ONLINE_FORCE_NAMES_SET,
  PROD_MODE,
  ROUTE_HISTORY_HOURS,
)
from decoder import _coords_are_zero, _normalize_role
from los import _haversine_m
from state import (
  DeviceState,
  device_names,
  device_roles,
  devices,
  mqtt_seen,
  route_history_segments,
  seen_devices,
)


def iso_from_ts(ts: Optional[float]) -> Optional[str]:
  """Convert Unix timestamp to ISO 8601 string."""
  if ts is None:
    return None
  try:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  except Exception:
    return None


def within_map_radius(lat: Any, lon: Any) -> bool:
  """Check if coordinates are within the configured map radius."""
  if MAP_RADIUS_KM <= 0:
    return True
  try:
    lat_val = float(lat)
    lon_val = float(lon)
  except (TypeError, ValueError):
    return False
  distance_m = _haversine_m(MAP_START_LAT, MAP_START_LON, lat_val, lon_val)
  return distance_m <= (MAP_RADIUS_KM * 1000.0)


def device_role_code(value: Any) -> int:
  """Convert role string/int to numeric code (1=companion, 2=repeater, 3=room)."""
  if isinstance(value, int):
    if value in (1, 2, 3):
      return value
    return 1
  if isinstance(value, str):
    trimmed = value.strip()
    if trimmed.isdigit():
      num = int(trimmed)
      if num in (1, 2, 3):
        return num
      return 1
    normalized = _normalize_role(trimmed)
    if normalized == "repeater":
      return 2
    if normalized == "room":
      return 3
    if normalized == "companion":
      return 1
  return 1


def parse_updated_since(value: Optional[str]) -> Optional[float]:
  """Parse ISO 8601 timestamp string to Unix timestamp."""
  if not value:
    return None
  try:
    text = value.strip()
    if text.endswith("Z"):
      text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).timestamp()
  except Exception:
    return None


def device_payload(device_id: str, state: "DeviceState") -> Dict[str, Any]:
  """Serialize device state for WebSocket/API response."""
  payload = asdict(state)
  last_seen = seen_devices.get(device_id)
  if last_seen:
    payload["last_seen_ts"] = last_seen
  else:
    payload["last_seen_ts"] = payload.get("ts")
  mqtt_seen_ts = mqtt_seen.get(device_id)
  if mqtt_seen_ts:
    payload["mqtt_seen_ts"] = mqtt_seen_ts
  if MQTT_ONLINE_FORCE_NAMES_SET:
    name_value = (state.name or device_names.get(device_id) or "").strip().lower()
    if name_value and name_value in MQTT_ONLINE_FORCE_NAMES_SET:
      payload["mqtt_forced"] = True
  if PROD_MODE:
    payload.pop("raw_topic", None)
  return payload


def node_api_payload(device_id: str, state: "DeviceState") -> Dict[str, Any]:
  """Serialize device state for /api/nodes endpoint."""
  last_seen = seen_devices.get(device_id) or state.ts
  last_seen_iso = iso_from_ts(last_seen)
  role_value = state.role or device_roles.get(device_id)
  role_code = device_role_code(role_value)
  return {
    "public_key": device_id,
    "name": (state.name or device_names.get(device_id) or ""),
    "device_role": role_code,
    "role": role_value,
    "location": {
      "latitude": float(state.lat),
      "longitude": float(state.lon),
    },
    "lat": state.lat,
    "lon": state.lon,
    "last_seen_ts": last_seen,
    "last_seen": last_seen_iso,
    "timestamp": int(last_seen) if last_seen else None,
    "first_seen": last_seen_iso,
    "battery_voltage": 0,
  }


def route_payload(route: Dict[str, Any]) -> Dict[str, Any]:
  """Serialize route for WebSocket/API response."""
  if not PROD_MODE:
    return route
  return {
    "id": route.get("id"),
    "points": route.get("points"),
    "route_mode": route.get("route_mode"),
    "ts": route.get("ts"),
    "expires_at": route.get("expires_at"),
    "payload_type": route.get("payload_type"),
  }


def history_edge_payload(edge: Dict[str, Any]) -> Dict[str, Any]:
  """Serialize history edge for WebSocket/API response."""
  return {
    "id": edge.get("id"),
    "a": edge.get("a"),
    "b": edge.get("b"),
    "count": edge.get("count"),
    "last_ts": edge.get("last_ts"),
    "recent": edge.get("recent") if isinstance(edge.get("recent"), list) else [],
  }


def peer_is_excluded(peer_id: str) -> bool:
  """Check if peer should be excluded from stats (forced-online nodes)."""
  if not MQTT_ONLINE_FORCE_NAMES_SET:
    return False
  state = devices.get(peer_id)
  name_value = ""
  if state and state.name:
    name_value = state.name
  if not name_value:
    name_value = device_names.get(peer_id) or ""
  if not name_value:
    return False
  return name_value.strip().lower() in MQTT_ONLINE_FORCE_NAMES_SET


def peer_device_payload(peer_id: str, count: int, total: int, last_ts: Optional[float]) -> Dict[str, Any]:
  """Serialize peer info for /peers endpoint."""
  state = devices.get(peer_id)
  name = None
  role = None
  lat = None
  lon = None
  if state:
    name = state.name or device_names.get(peer_id)
    role = state.role or device_roles.get(peer_id)
    if not _coords_are_zero(state.lat, state.lon):
      lat = float(state.lat)
      lon = float(state.lon)
  if not name:
    name = device_names.get(peer_id)
  percent = (count / total * 100.0) if total > 0 else 0.0
  return {
    "peer_id": peer_id,
    "name": name or "",
    "role": role,
    "lat": lat,
    "lon": lon,
    "count": int(count),
    "percent": round(percent, 1),
    "last_seen_ts": last_ts,
  }


def peer_stats_for_device(device_id: str, limit: int) -> Dict[str, Any]:
  """Calculate inbound/outbound peer statistics for a device."""
  inbound: Dict[str, int] = {}
  outbound: Dict[str, int] = {}
  inbound_last: Dict[str, float] = {}
  outbound_last: Dict[str, float] = {}

  for entry in route_history_segments:
    if not isinstance(entry, dict):
      continue
    a_id = entry.get("a_id")
    b_id = entry.get("b_id")
    if not a_id or not b_id:
      continue
    ts = entry.get("ts") or 0
    if a_id == device_id and b_id != device_id:
      if peer_is_excluded(b_id):
        continue
      outbound[b_id] = outbound.get(b_id, 0) + 1
      outbound_last[b_id] = max(outbound_last.get(b_id, 0), float(ts))
    if b_id == device_id and a_id != device_id:
      if peer_is_excluded(a_id):
        continue
      inbound[a_id] = inbound.get(a_id, 0) + 1
      inbound_last[a_id] = max(inbound_last.get(a_id, 0), float(ts))

  inbound_total = sum(inbound.values())
  outbound_total = sum(outbound.values())

  inbound_items = [
    peer_device_payload(peer_id, count, inbound_total, inbound_last.get(peer_id))
    for peer_id, count in inbound.items()
  ]
  outbound_items = [
    peer_device_payload(peer_id, count, outbound_total, outbound_last.get(peer_id))
    for peer_id, count in outbound.items()
  ]
  inbound_items.sort(key=lambda item: item.get("count", 0), reverse=True)
  outbound_items.sort(key=lambda item: item.get("count", 0), reverse=True)

  if limit > 0:
    inbound_items = inbound_items[:limit]
    outbound_items = outbound_items[:limit]

  return {
    "device_id": device_id,
    "incoming_total": inbound_total,
    "outgoing_total": outbound_total,
    "incoming": inbound_items,
    "outgoing": outbound_items,
    "window_hours": ROUTE_HISTORY_HOURS,
  }
