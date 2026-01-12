import base64
import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from config import (
  APP_DIR,
  DECODE_WITH_NODE,
  DIRECT_COORDS_ALLOW_ZERO,
  DIRECT_COORDS_MODE,
  DIRECT_COORDS_TOPIC_REGEX,
  HEAT_TTL_SECONDS,
  MQTT_ONLINE_TOPIC_SUFFIXES,
  NODE_DECODE_TIMEOUT_SECONDS,
  NODE_SCRIPT_PATH,
  PAYLOAD_PREVIEW_MAX,
  ROUTE_PATH_MAX_LEN,
  ROUTE_PAYLOAD_TYPES,
)
from state import (
  devices,
  heat_events,
  node_hash_candidates,
  node_hash_collisions,
  node_hash_to_device,
  seen_devices,
)

LATLON_KEYS_LAT = ("lat", "latitude")
LATLON_KEYS_LON = ("lon", "lng", "longitude")

# e.g. "lat 42.3601 lon -71.0589" or "lat=42.36 lon=-71.05"
RE_LAT_LON = re.compile(
  r"\blat(?:itude)?\b\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*[, ]+\s*\b(?:lon|lng|longitude)\b\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
  re.IGNORECASE,
)

# e.g. "42.3601 -71.0589" (two floats)
RE_TWO_FLOATS = re.compile(
  r"(-?\d{1,2}\.\d+)\s*[,\s]+\s*(-?\d{1,3}\.\d+)"
)

BASE64_LIKE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
NODE_HASH_RE = re.compile(r"^[0-9a-fA-F]{2}$")

_node_ready_once = False
_node_unavailable_once = False

ROUTE_PAYLOAD_TYPES_SET: Set[int] = set()
for _part in ROUTE_PAYLOAD_TYPES.split(","):
  _part = _part.strip()
  if not _part:
    continue
  try:
    ROUTE_PAYLOAD_TYPES_SET.add(int(_part))
  except ValueError:
    pass

LIKELY_PACKET_KEYS = (
  "hex", "raw", "packet", "packet_hex", "frame", "data", "payload",
  "mesh_packet", "meshcore_packet", "rx_packet", "bytes", "packet_bytes",
)

try:
  DIRECT_COORDS_TOPIC_RE = re.compile(DIRECT_COORDS_TOPIC_REGEX, re.IGNORECASE)
except re.error:
  DIRECT_COORDS_TOPIC_RE = None


def _valid_lat_lon(lat: float, lon: float) -> bool:
  return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _normalize_lat_lon(lat: Any, lon: Any) -> Optional[Tuple[float, float]]:
  try:
    latf = float(lat)
    lonf = float(lon)
  except Exception:
    return None

  if _valid_lat_lon(latf, lonf):
    return latf, lonf

  for scale in (1e7, 1e6, 1e5, 1e4):
    lat2 = latf / scale
    lon2 = lonf / scale
    if _valid_lat_lon(lat2, lon2):
      return lat2, lon2

  return None


def _coords_are_zero(lat: Any, lon: Any) -> bool:
  try:
    lat_val = float(lat)
    lon_val = float(lon)
  except (TypeError, ValueError):
    return False
  return abs(lat_val) < 1e-6 and abs(lon_val) < 1e-6


def _find_lat_lon_in_json(obj: Any) -> Optional[Tuple[float, float]]:
  """
  Recursively walk JSON objects/lists looking for lat/lon keys.
  """
  if isinstance(obj, dict):
    lat = None
    lon = None
    for k in LATLON_KEYS_LAT:
      if k in obj:
        lat = obj.get(k)
        break
    for k in LATLON_KEYS_LON:
      if k in obj:
        lon = obj.get(k)
        break
    if lat is not None and lon is not None:
      normalized = _normalize_lat_lon(lat, lon)
      if normalized:
        return normalized

    for v in obj.values():
      found = _find_lat_lon_in_json(v)
      if found:
        return found

  elif isinstance(obj, list):
    for v in obj:
      found = _find_lat_lon_in_json(v)
      if found:
        return found

  return None


def _strings_from_json(obj: Any) -> List[str]:
  """
  Collect all string leaves from a JSON-like structure.
  """
  out: List[str] = []
  if isinstance(obj, str):
    out.append(obj)
  elif isinstance(obj, dict):
    for v in obj.values():
      out.extend(_strings_from_json(v))
  elif isinstance(obj, list):
    for v in obj:
      out.extend(_strings_from_json(v))
  return out


def _find_lat_lon_in_text(text: str) -> Optional[Tuple[float, float]]:
  """
  Try to extract coordinates from a text blob.
  """
  m = RE_LAT_LON.search(text)
  if m:
    normalized = _normalize_lat_lon(m.group(1), m.group(2))
    if normalized:
      return normalized

  for m2 in RE_TWO_FLOATS.finditer(text):
    normalized = _normalize_lat_lon(m2.group(1), m2.group(2))
    if normalized:
      return normalized

  return None


def _maybe_base64_decode_to_text(s: str) -> Optional[str]:
  """
  Best-effort: if a string looks base64-ish, try decoding to UTF-8-ish text.
  """
  s_stripped = s.strip()
  if len(s_stripped) < 24:
    return None
  if not BASE64_LIKE.match(s_stripped):
    return None

  try:
    raw = base64.b64decode(s_stripped, validate=False)
    return raw.decode("utf-8", errors="ignore")
  except Exception:
    return None


def _looks_like_hex(s: str) -> bool:
  s2 = s.strip()
  if len(s2) < 20:
    return False
  if len(s2) % 2 != 0:
    return False
  return bool(re.fullmatch(r"[0-9a-fA-F]+", s2))


def _try_base64_to_hex(s: str) -> Optional[str]:
  s2 = s.strip()
  if len(s2) < 24:
    return None
  if not any(c in s2 for c in "+/="):
    return None
  try:
    raw = base64.b64decode(s2, validate=False)
    if len(raw) < 10:
      return None
    return raw.hex()
  except Exception:
    return None


def _is_probably_binary(data: bytes) -> bool:
  if not data:
    return False
  printable = 0
  for b in data[:200]:
    if 32 <= b <= 126 or b in (9, 10, 13):
      printable += 1
  return printable / min(len(data), 200) < 0.6


def _safe_preview(data: bytes) -> str:
  try:
    text = data.decode("utf-8", errors="replace")
  except Exception:
    text = repr(data)
  if len(text) > PAYLOAD_PREVIEW_MAX:
    return text[:PAYLOAD_PREVIEW_MAX] + "..."
  return text


def _normalize_node_hash(value: Any) -> Optional[str]:
  if value is None:
    return None
  if isinstance(value, int):
    return f"{value:02X}"
  s = str(value).strip()
  if s.lower().startswith("0x"):
    s = s[2:]
  if len(s) == 1:
    s = f"0{s}"
  if len(s) != 2 or not NODE_HASH_RE.match(s):
    return None
  return s.upper()


def _node_hash_from_device_id(device_id: str) -> Optional[str]:
  if not device_id or len(device_id) < 2:
    return None
  return _normalize_node_hash(device_id[:2])


def _rebuild_node_hash_map() -> None:
  candidates: Dict[str, List[str]] = {}
  collisions: Set[str] = set()
  for device_id in devices.keys():
    node_hash = _node_hash_from_device_id(device_id)
    if not node_hash:
      continue
    candidates.setdefault(node_hash, []).append(device_id)
  mapping: Dict[str, str] = {}
  for node_hash, ids in candidates.items():
    if len(ids) == 1:
      mapping[node_hash] = ids[0]
    else:
      collisions.add(node_hash)
  node_hash_candidates.clear()
  node_hash_candidates.update(candidates)
  node_hash_collisions.clear()
  node_hash_collisions.update(collisions)
  node_hash_to_device.clear()
  node_hash_to_device.update(mapping)


def _choose_device_for_hash(node_hash: str, ts: float) -> Optional[str]:
  candidates = node_hash_candidates.get(node_hash)
  if not candidates:
    return None
  best_id = None
  best_delta = None
  for device_id in candidates:
    state = devices.get(device_id)
    if not state:
      continue
    if _coords_are_zero(state.lat, state.lon):
      continue
    last_seen = seen_devices.get(device_id) or state.ts or 0.0
    try:
      delta = abs(float(last_seen) - float(ts))
    except (TypeError, ValueError):
      delta = None
    if delta is None:
      continue
    if best_delta is None or delta < best_delta:
      best_delta = delta
      best_id = device_id
  return best_id


def _route_points_from_hashes(path_hashes: List[Any], origin_id: Optional[str], receiver_id: Optional[str], ts: float) -> Tuple[Optional[List[List[float]]], List[str], List[Optional[str]]]:
  normalized: List[str] = []
  for raw in path_hashes:
    key = _normalize_node_hash(raw)
    if key:
      normalized.append(key)
  if ROUTE_PATH_MAX_LEN > 0 and len(normalized) > ROUTE_PATH_MAX_LEN:
    return None, []

  receiver_hash = _node_hash_from_device_id(receiver_id) if receiver_id else None
  origin_hash = _node_hash_from_device_id(origin_id) if origin_id else None

  if receiver_hash and receiver_hash in normalized:
    if normalized and normalized[0] == receiver_hash and normalized[-1] != receiver_hash:
      normalized.reverse()
  elif origin_hash and origin_hash in normalized:
    if normalized and normalized[-1] == origin_hash and normalized[0] != origin_hash:
      normalized.reverse()

  points: List[List[float]] = []
  used_hashes: List[str] = []
  point_ids: List[Optional[str]] = []

  for key in normalized:
    device_id = node_hash_to_device.get(key)
    if not device_id:
      continue
    state = devices.get(device_id)
    if not state:
      continue
    if _coords_are_zero(state.lat, state.lon):
      continue
    point = [state.lat, state.lon]
    if points and point == points[-1]:
      continue
    points.append(point)
    used_hashes.append(key)
    point_ids.append(device_id)

  origin_point = None
  if origin_id:
    origin_state = devices.get(origin_id)
    if origin_state and not _coords_are_zero(origin_state.lat, origin_state.lon):
      origin_point = [origin_state.lat, origin_state.lon]
      if not points or points[0] != origin_point:
        points.insert(0, origin_point)
        point_ids.insert(0, origin_id)
      elif point_ids:
        point_ids[0] = origin_id

  receiver_point = None
  if receiver_id:
    receiver_state = devices.get(receiver_id)
    if receiver_state and not _coords_are_zero(receiver_state.lat, receiver_state.lon):
      receiver_point = [receiver_state.lat, receiver_state.lon]
      if points and receiver_point != points[-1]:
        points.append(receiver_point)
        point_ids.append(receiver_id)
      elif point_ids:
        point_ids[-1] = receiver_id

  if len(points) < 2:
    return None, used_hashes, point_ids

  return points, used_hashes, point_ids


def _route_points_from_device_ids(origin_id: Optional[str], receiver_id: Optional[str]) -> Optional[List[List[float]]]:
  if not origin_id or not receiver_id or origin_id == receiver_id:
    return None
  origin_state = devices.get(origin_id)
  receiver_state = devices.get(receiver_id)
  if not origin_state or not receiver_state:
    return None
  if _coords_are_zero(origin_state.lat, origin_state.lon) or _coords_are_zero(receiver_state.lat, receiver_state.lon):
    return None
  points = [
    [origin_state.lat, origin_state.lon],
    [receiver_state.lat, receiver_state.lon],
  ]
  if points[0] == points[1]:
    return None
  return points


def _append_heat_points(points: List[List[float]], ts: float, payload_type: Optional[int]) -> None:
  if HEAT_TTL_SECONDS <= 0:
    return
  for point in points:
    heat_events.append({
      "lat": float(point[0]),
      "lon": float(point[1]),
      "ts": float(ts),
      "weight": 0.7,
    })


def _serialize_heat_events() -> List[List[float]]:
  if HEAT_TTL_SECONDS <= 0:
    return []
  cutoff = time.time() - HEAT_TTL_SECONDS
  return [
    [entry.get("lat"), entry.get("lon"), entry.get("ts"), entry.get("weight", 0.7)]
    for entry in heat_events
    if entry.get("ts", 0) >= cutoff
  ]



def _extract_device_name(obj: Any, topic: str) -> Optional[str]:
  if not isinstance(obj, dict):
    return None

  for key in (
    "name",
    "device_name",
    "deviceName",
    "node_name",
    "nodeName",
    "display_name",
    "displayName",
    "callsign",
    "label",
  ):
    value = obj.get(key)
    if isinstance(value, str) and value.strip():
      return value.strip()

  if topic.endswith("/status"):
    origin = obj.get("origin")
    if isinstance(origin, str) and origin.strip():
      return origin.strip()

  return None


def _normalize_role(value: str) -> Optional[str]:
  s = value.strip().lower()
  if not s:
    return None
  if "repeater" in s or s in ("repeat", "relay"):
    return "repeater"
  if "companion" in s or "chat node" in s or "chatnode" in s or s == "chat":
    return "companion"
  if "room server" in s or "roomserver" in s or "room" in s:
    return "room"
  return None


def _extract_device_role(obj: Any, topic: str) -> Optional[str]:
  if not isinstance(obj, dict):
    return None

  for key in (
    "role",
    "device_role",
    "deviceRole",
    "node_role",
    "nodeRole",
    "node_type",
    "nodeType",
    "device_type",
    "deviceType",
    "class",
    "profile",
  ):
    value = obj.get(key)
    if isinstance(value, str):
      role = _normalize_role(value)
      if role:
        return role

  return None


def _apply_meta_role(debug: Dict[str, Any], meta: Optional[Dict[str, Any]]) -> None:
  if debug.get("device_role"):
    return
  if not isinstance(meta, dict):
    return
  role_value = meta.get("role") or meta.get("deviceRoleName")
  if role_value is None:
    device_role_code = meta.get("deviceRole")
    if isinstance(device_role_code, int):
      if device_role_code == 2:
        role_value = "repeater"
      elif device_role_code == 3:
        role_value = "room"
      elif device_role_code == 1:
        role_value = "companion"
  if isinstance(role_value, str):
    normalized = _normalize_role(role_value)
    if normalized:
      debug["device_role"] = normalized

def _has_location_hints(obj: Any) -> bool:
  if isinstance(obj, dict):
    for k, v in obj.items():
      key = str(k).lower()
      if key in ("location", "gps", "position", "coords", "coordinate", "geo", "geolocation", "latlon"):
        return True
      if isinstance(v, (dict, list)) and _has_location_hints(v):
        return True
  elif isinstance(obj, list):
    for v in obj:
      if _has_location_hints(v):
        return True
  return False


def _topic_marks_online(topic: str) -> bool:
  if not MQTT_ONLINE_TOPIC_SUFFIXES:
    return False
  return any(topic.endswith(suffix) for suffix in MQTT_ONLINE_TOPIC_SUFFIXES)


def _direct_coords_allowed(topic: str, obj: Any) -> bool:
  if DIRECT_COORDS_MODE == "off":
    return False
  if DIRECT_COORDS_MODE == "any":
    return True
  if DIRECT_COORDS_MODE in ("topic", "strict"):
    if DIRECT_COORDS_TOPIC_RE and DIRECT_COORDS_TOPIC_RE.search(topic):
      return True
    if DIRECT_COORDS_MODE == "topic":
      return False
    return _has_location_hints(obj)
  return True


# =========================
# MeshCore decoder via Node
# =========================

def _ensure_node_decoder() -> bool:
  """
  Verify that the Node.js decoder is available and ready to use.

  Checks:
  1. DECODE_WITH_NODE is enabled
  2. Node.js is installed
  3. @michaelhart/meshcore-decoder package is available
  4. The decoder script exists at scripts/meshcore_decode.mjs

  Returns True if decoder is ready, False otherwise.
  """
  global _node_ready_once, _node_unavailable_once

  if not DECODE_WITH_NODE:
    return False
  if _node_ready_once:
    return True
  if _node_unavailable_once:
    return False

  # Check Node.js is available
  try:
    subprocess.run(["node", "-v"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  except Exception:
    _node_unavailable_once = True
    print("[decode] node not found in container")
    return False

  # Check meshcore-decoder package is installed
  try:
    subprocess.run(
      ["node", "--input-type=module", "-e", "import('@michaelhart/meshcore-decoder')"],
      check=True,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      cwd=APP_DIR,
    )
  except Exception:
    _node_unavailable_once = True
    print("[decode] @michaelhart/meshcore-decoder not available")
    return False

  # Check external script exists
  if not os.path.exists(NODE_SCRIPT_PATH):
    _node_unavailable_once = True
    print(f"[decode] decoder script not found at {NODE_SCRIPT_PATH}")
    return False

  _node_ready_once = True
  print("[decode] node decoder ready")
  return True


def _decode_meshcore_hex(hex_str: str) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str], Dict[str, Any]]:
  if not _ensure_node_decoder():
    return (None, None, None, None, {"ok": False, "error": "node_decoder_unavailable"})

  try:
    proc = subprocess.run(
      ["node", NODE_SCRIPT_PATH, hex_str],
      capture_output=True,
      text=True,
      timeout=NODE_DECODE_TIMEOUT_SECONDS,
      cwd=APP_DIR,
    )
  except Exception as exc:
    return (None, None, None, None, {"ok": False, "error": str(exc)})

  out = (proc.stdout or "").strip()
  if not out:
    return (None, None, None, None, {"ok": False, "error": "empty_decoder_output"})

  try:
    data = json.loads(out)
  except Exception:
    return (None, None, None, None, {"ok": False, "error": "decoder_output_not_json", "output": out})

  if not data.get("ok"):
    return (None, None, None, None, {"ok": False, **data})

  loc = data.get("location") or {}
  lat = loc.get("lat")
  lon = loc.get("lon")
  name = loc.get("name")
  pubkey = loc.get("pubkey")

  normalized = None
  if lat is not None and lon is not None:
    normalized = _normalize_lat_lon(lat, lon)

  if normalized:
    return (normalized[0], normalized[1], pubkey, name, {"ok": True, **data})

  return (None, None, pubkey, name, {"ok": True, **data, "note": "decoded_no_location"})


# =========================
# Parsing: MeshCore-ish payloads
# =========================

def _device_id_from_topic(topic: str) -> Optional[str]:
  parts = topic.split("/")
  if len(parts) >= 3 and parts[0] == "meshcore":
    return parts[2]
  return None


def _find_packet_blob(obj: Any, path: str = "root") -> Tuple[Optional[str], Optional[str], Optional[str]]:
  if isinstance(obj, str):
    if _looks_like_hex(obj):
      return (obj.strip(), path, "hex")
    b64hex = _try_base64_to_hex(obj)
    if b64hex:
      return (b64hex, path, "base64")
    return (None, None, None)

  if isinstance(obj, list):
    if obj and all(isinstance(x, int) for x in obj[: min(20, len(obj))]):
      try:
        raw = bytes(obj)
        if len(raw) >= 10:
          return (raw.hex(), path, "list[int]")
      except Exception:
        pass
    for idx, v in enumerate(obj):
      sub_path = f"{path}[{idx}]"
      hex_str, where, hint = _find_packet_blob(v, sub_path)
      if hex_str:
        return (hex_str, where, hint)
    return (None, None, None)

  if isinstance(obj, dict):
    keys = list(obj.keys())
    keys.sort(key=lambda k: 0 if k in LIKELY_PACKET_KEYS else 1)
    for k in keys:
      v = obj.get(k)
      sub_path = f"{path}.{k}"
      if isinstance(v, str):
        if _looks_like_hex(v):
          return (v.strip(), sub_path, "hex")
        b64hex = _try_base64_to_hex(v)
        if b64hex:
          return (b64hex, sub_path, "base64")
      if isinstance(v, list) and v and all(isinstance(x, int) for x in v[: min(20, len(v))]):
        try:
          raw = bytes(v)
          if len(raw) >= 10:
            return (raw.hex(), sub_path, "list[int]")
        except Exception:
          pass
      if isinstance(v, (dict, list)):
        hex_str, where, hint = _find_packet_blob(v, sub_path)
        if hex_str:
          return (hex_str, where, hint)

  return (None, None, None)


def _extract_device_id(obj: Any, topic: str, decoded_pubkey: Optional[str]) -> str:
  if decoded_pubkey:
    return str(decoded_pubkey)
  if isinstance(obj, dict):
    device_id = obj.get("device_id") or obj.get("id") or obj.get("from") or obj.get("origin_id")
    if device_id:
      return str(device_id)
    jwt = obj.get("jwt_payload")
    if isinstance(jwt, dict) and jwt.get("publickey"):
      return str(jwt.get("publickey"))
  return _device_id_from_topic(topic) or topic.split("/")[-1]


def _try_parse_payload(topic: str, payload_bytes: bytes) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
  debug: Dict[str, Any] = {
    "result": "no_coords",
    "found_path": None,
    "found_hint": None,
    "decoder_meta": None,
    "json_keys": None,
    "parse_error": None,
    "origin_id": None,
    "device_name": None,
    "device_role": None,
    "decoded_pubkey": None,
    "packet_hash": None,
    "direction": None,
    "packet_type": None,
  }

  text = None
  try:
    text = payload_bytes.decode("utf-8", errors="strict").strip()
  except Exception:
    text = payload_bytes.decode("utf-8", errors="ignore").strip()

  obj = None
  if text and text.startswith("{") and text.endswith("}"):
    try:
      obj = json.loads(text)
      if isinstance(obj, dict):
        debug["json_keys"] = list(obj.keys())[:50]
        debug["origin_id"] = obj.get("origin_id") or obj.get("originId")
        debug["device_name"] = _extract_device_name(obj, topic)
        debug["device_role"] = _extract_device_role(obj, topic)
        debug["direction"] = obj.get("direction")
        debug["packet_hash"] = obj.get("hash") or obj.get("message_hash") or obj.get("messageHash")
        debug["packet_type"] = obj.get("packet_type") or obj.get("packetType") or obj.get("type")
    except Exception as exc:
      debug["parse_error"] = str(exc)

  if obj is not None:
    found = _find_lat_lon_in_json(obj)
    if found:
      if not _direct_coords_allowed(topic, obj):
        debug["result"] = "direct_blocked"
        return (None, debug)
      if not DIRECT_COORDS_ALLOW_ZERO and _coords_are_zero(found[0], found[1]):
        debug["result"] = "direct_zero_coords"
        return (None, debug)
      device_id = _extract_device_id(obj, topic, None)
      ts = time.time()
      if isinstance(obj, dict):
        tval = obj.get("ts") or obj.get("time") or obj.get("timestamp")
        if isinstance(tval, (int, float)):
          ts = float(tval)
      debug["result"] = "direct_json"
      return ({
        "device_id": device_id,
        "lat": found[0],
        "lon": found[1],
        "ts": ts,
        "heading": obj.get("heading") if isinstance(obj, dict) else None,
        "speed": obj.get("speed") if isinstance(obj, dict) else None,
        "rssi": obj.get("rssi") if isinstance(obj, dict) else None,
        "snr": obj.get("snr") if isinstance(obj, dict) else None,
        "role": debug.get("device_role"),
      }, debug)

    for s in _strings_from_json(obj):
      got = _find_lat_lon_in_text(s)
      if got:
        if not _direct_coords_allowed(topic, obj):
          debug["result"] = "direct_blocked"
          return (None, debug)
        if not DIRECT_COORDS_ALLOW_ZERO and _coords_are_zero(got[0], got[1]):
          debug["result"] = "direct_zero_coords"
          return (None, debug)
        device_id = _extract_device_id(obj, topic, None)
        debug["result"] = "direct_text_json"
        return ({
          "device_id": device_id,
          "lat": got[0],
          "lon": got[1],
          "ts": time.time(),
          "role": debug.get("device_role"),
        }, debug)

      decoded = _maybe_base64_decode_to_text(s)
      if decoded:
        got2 = _find_lat_lon_in_text(decoded)
        if got2:
          if not _direct_coords_allowed(topic, obj):
            debug["result"] = "direct_blocked"
            return (None, debug)
          if not DIRECT_COORDS_ALLOW_ZERO and _coords_are_zero(got2[0], got2[1]):
            debug["result"] = "direct_zero_coords"
            return (None, debug)
          device_id = _extract_device_id(obj, topic, None)
          debug["result"] = "direct_text_json_base64"
          return ({
            "device_id": device_id,
            "lat": got2[0],
            "lon": got2[1],
            "ts": time.time(),
            "role": debug.get("device_role"),
          }, debug)

    hex_str, where, hint = _find_packet_blob(obj)
    debug["found_path"] = where
    debug["found_hint"] = hint
    if hex_str:
      lat, lon, decoded_pubkey, name, meta = _decode_meshcore_hex(hex_str)
      debug["decoded_pubkey"] = decoded_pubkey
      debug["decoder_meta"] = meta
      _apply_meta_role(debug, meta)
      if lat is not None and lon is not None:
        device_id = _extract_device_id(obj, topic, decoded_pubkey)
        debug["result"] = "decoded"
        return ({
          "device_id": device_id,
          "lat": lat,
          "lon": lon,
          "ts": time.time(),
          "rssi": obj.get("rssi") if isinstance(obj, dict) else None,
          "snr": obj.get("snr") if isinstance(obj, dict) else None,
          "name": name,
          "role": debug.get("device_role"),
        }, debug)
      debug["result"] = "decoded_no_location" if meta.get("ok") else "decode_failed"
      return (None, debug)

    debug["result"] = "json_no_packet_blob"
    return (None, debug)

  if text:
    got = _find_lat_lon_in_text(text)
    if got:
      if not _direct_coords_allowed(topic, None):
        debug["result"] = "direct_blocked"
        return (None, debug)
      if not DIRECT_COORDS_ALLOW_ZERO and _coords_are_zero(got[0], got[1]):
        debug["result"] = "direct_zero_coords"
        return (None, debug)
      debug["result"] = "direct_text"
      return ({
        "device_id": _extract_device_id(None, topic, None),
        "lat": got[0],
        "lon": got[1],
        "ts": time.time(),
        "role": debug.get("device_role"),
      }, debug)

    if _looks_like_hex(text):
      debug["found_path"] = "payload"
      debug["found_hint"] = "hex"
      lat, lon, decoded_pubkey, name, meta = _decode_meshcore_hex(text.strip())
      debug["decoded_pubkey"] = decoded_pubkey
      debug["decoder_meta"] = meta
      _apply_meta_role(debug, meta)
      if lat is not None and lon is not None:
        debug["result"] = "decoded"
        return ({
          "device_id": _extract_device_id(None, topic, decoded_pubkey),
          "lat": lat,
          "lon": lon,
          "ts": time.time(),
          "name": name,
          "role": debug.get("device_role"),
        }, debug)
      debug["result"] = "decoded_no_location" if meta.get("ok") else "decode_failed"
      return (None, debug)

    b64hex = _try_base64_to_hex(text)
    if b64hex:
      debug["found_path"] = "payload"
      debug["found_hint"] = "base64"
      lat, lon, decoded_pubkey, name, meta = _decode_meshcore_hex(b64hex)
      debug["decoded_pubkey"] = decoded_pubkey
      debug["decoder_meta"] = meta
      _apply_meta_role(debug, meta)
      if lat is not None and lon is not None:
        debug["result"] = "decoded"
        return ({
          "device_id": _extract_device_id(None, topic, decoded_pubkey),
          "lat": lat,
          "lon": lon,
          "ts": time.time(),
          "name": name,
          "role": debug.get("device_role"),
        }, debug)
      debug["result"] = "decoded_no_location" if meta.get("ok") else "decode_failed"
      return (None, debug)

  if _is_probably_binary(payload_bytes) and len(payload_bytes) >= 10:
    debug["found_path"] = "payload_bytes"
    debug["found_hint"] = "raw_bytes"
    lat, lon, decoded_pubkey, name, meta = _decode_meshcore_hex(payload_bytes.hex())
    debug["decoded_pubkey"] = decoded_pubkey
    debug["decoder_meta"] = meta
    _apply_meta_role(debug, meta)
    if lat is not None and lon is not None:
      debug["result"] = "decoded"
      return ({
        "device_id": _extract_device_id(None, topic, decoded_pubkey),
        "lat": lat,
        "lon": lon,
        "ts": time.time(),
        "name": name,
        "role": debug.get("device_role"),
      }, debug)
    debug["result"] = "decoded_no_location" if meta.get("ok") else "decode_failed"
    return (None, debug)

  return (None, debug)
