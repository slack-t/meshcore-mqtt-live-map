import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import state
from config import (
  HISTORY_EDGE_SAMPLE_LIMIT,
  ROUTE_HISTORY_ALLOWED_MODES_SET,
  ROUTE_HISTORY_COMPACT_INTERVAL,
  ROUTE_HISTORY_ENABLED,
  ROUTE_HISTORY_FILE,
  ROUTE_HISTORY_HOURS,
  ROUTE_HISTORY_MAX_SEGMENTS,
  ROUTE_HISTORY_PAYLOAD_TYPES,
)
from decoder import _coords_are_zero
from los import _haversine_m
from config import MAP_RADIUS_KM, MAP_START_LAT, MAP_START_LON

ROUTE_HISTORY_PAYLOAD_TYPES_SET: Set[int] = set()
for _part in ROUTE_HISTORY_PAYLOAD_TYPES.split(","):
  _part = _part.strip()
  if not _part:
    continue
  try:
    ROUTE_HISTORY_PAYLOAD_TYPES_SET.add(int(_part))
  except ValueError:
    pass


def _history_payload_allowed(payload_type: Optional[int]) -> bool:
  if not ROUTE_HISTORY_ENABLED or ROUTE_HISTORY_HOURS <= 0:
    return False
  if not ROUTE_HISTORY_PAYLOAD_TYPES_SET:
    return True
  if payload_type is None:
    return False
  return payload_type in ROUTE_HISTORY_PAYLOAD_TYPES_SET


def _within_map_radius(lat: float, lon: float) -> bool:
  if MAP_RADIUS_KM <= 0:
    return True
  distance_m = _haversine_m(MAP_START_LAT, MAP_START_LON, lat, lon)
  return distance_m <= (MAP_RADIUS_KM * 1000.0)


def _normalize_history_point(point: Any) -> Optional[Tuple[float, float]]:
  if not isinstance(point, (list, tuple)) or len(point) < 2:
    return None
  try:
    lat_val = float(point[0])
    lon_val = float(point[1])
  except (TypeError, ValueError):
    return None
  if _coords_are_zero(lat_val, lon_val):
    return None
  if not _within_map_radius(lat_val, lon_val):
    return None
  return (round(lat_val, 6), round(lon_val, 6))


def _history_edge_key(
  a: Tuple[float, float], b: Tuple[float, float]
) -> Tuple[str, Tuple[float, float], Tuple[float, float]]:
  if a <= b:
    key = f"{a[0]:.6f},{a[1]:.6f}|{b[0]:.6f},{b[1]:.6f}"
    return key, a, b
  key = f"{b[0]:.6f},{b[1]:.6f}|{a[0]:.6f},{a[1]:.6f}"
  return key, b, a


def _history_sample_from_route(route: Dict[str, Any],
                               ts: float) -> Dict[str, Any]:
  return {
    "ts": float(ts),
    "message_hash": route.get("message_hash"),
    "payload_type": route.get("payload_type"),
    "origin_id": route.get("origin_id"),
    "receiver_id": route.get("receiver_id"),
    "route_mode": route.get("route_mode"),
    "topic": route.get("topic"),
  }


def _update_history_edge_recent(
  edge: Dict[str, Any], sample: Dict[str, Any]
) -> None:
  if not edge or not sample:
    return
  recent = edge.get("recent")
  if not isinstance(recent, list):
    recent = []
  recent.append(sample)
  recent.sort(key=lambda s: s.get("ts", 0), reverse=True)
  if len(recent) > HISTORY_EDGE_SAMPLE_LIMIT:
    recent = recent[:HISTORY_EDGE_SAMPLE_LIMIT]
  edge["recent"] = recent


def _record_route_history(
  route: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[str]]:
  if not ROUTE_HISTORY_ENABLED:
    return [], []
  if ROUTE_HISTORY_ALLOWED_MODES_SET:
    route_mode = route.get("route_mode")
    if not route_mode or route_mode not in ROUTE_HISTORY_ALLOWED_MODES_SET:
      return [], []
  payload_type = route.get("payload_type")
  if not _history_payload_allowed(payload_type):
    return [], []
  points = route.get("points")
  point_ids = route.get("point_ids"
                       ) if isinstance(route.get("point_ids"), list) else None
  if not isinstance(points, list) or len(points) < 2:
    return [], []

  ts = route.get("ts") or time.time()
  sample = _history_sample_from_route(route, ts)
  updated_keys: Set[str] = set()
  new_entries: List[Dict[str, Any]] = []

  for idx in range(len(points) - 1):
    a = _normalize_history_point(points[idx])
    b = _normalize_history_point(points[idx + 1])
    if not a or not b:
      continue
    a_id = None
    b_id = None
    if point_ids and idx < len(point_ids) - 1:
      a_id = point_ids[idx]
      b_id = point_ids[idx + 1]
    key, first, second = _history_edge_key(a, b)
    new_entries.append(
      {
        "ts": float(ts),
        "a": [first[0], first[1]],
        "b": [second[0], second[1]],
        "a_id": a_id,
        "b_id": b_id,
        "message_hash": sample.get("message_hash"),
        "payload_type": sample.get("payload_type"),
        "origin_id": sample.get("origin_id"),
        "receiver_id": sample.get("receiver_id"),
        "route_mode": sample.get("route_mode"),
        "topic": sample.get("topic"),
      }
    )
    edge = state.route_history_edges.get(key)
    if not edge:
      edge = {
        "id": key,
        "a": [first[0], first[1]],
        "b": [second[0], second[1]],
        "count": 0,
        "last_ts": float(ts),
      }
      state.route_history_edges[key] = edge
    edge["count"] = int(edge.get("count", 0)) + 1
    edge["last_ts"] = max(edge.get("last_ts", float(ts)), float(ts))
    _update_history_edge_recent(edge, sample)
    updated_keys.add(key)

  if not new_entries:
    return [], []

  state.route_history_segments.extend(new_entries)
  _append_route_history_file(new_entries)

  updates = [
    state.route_history_edges[key]
    for key in updated_keys if key in state.route_history_edges
  ]
  removed: List[str] = []
  if ROUTE_HISTORY_MAX_SEGMENTS > 0 and len(
    state.route_history_segments
  ) > ROUTE_HISTORY_MAX_SEGMENTS:
    extra_updates, extra_removed = _prune_route_history(force_limit=True)
    updates.extend(extra_updates)
    removed.extend(extra_removed)

  return updates, removed


def _prune_route_history(
  force_limit: bool = False
) -> Tuple[List[Dict[str, Any]], List[str]]:
  if not ROUTE_HISTORY_ENABLED or not state.route_history_segments:
    return [], []

  updated: Dict[str, Dict[str, Any]] = {}
  removed: List[str] = []
  now = time.time()
  cutoff = now - (ROUTE_HISTORY_HOURS * 3600)

  while state.route_history_segments:
    entry = state.route_history_segments[0]
    if not isinstance(entry, dict):
      state.route_history_segments.popleft()
      continue
    ts = entry.get("ts")
    if ts is None:
      state.route_history_segments.popleft()
      continue
    if not force_limit and ts >= cutoff:
      break
    if force_limit and ROUTE_HISTORY_MAX_SEGMENTS > 0 and len(
      state.route_history_segments
    ) <= ROUTE_HISTORY_MAX_SEGMENTS:
      break
    state.route_history_segments.popleft()
    a = entry.get("a")
    b = entry.get("b")
    a_point = _normalize_history_point(a) if a else None
    b_point = _normalize_history_point(b) if b else None
    if not a_point or not b_point:
      state.route_history_compact = True
      continue
    key, _, _ = _history_edge_key(a_point, b_point)
    edge = state.route_history_edges.get(key)
    if not edge:
      state.route_history_compact = True
      continue
    edge["count"] = int(edge.get("count", 0)) - 1
    recent = edge.get("recent")
    if isinstance(recent, list):
      edge["recent"] = [s for s in recent if (s.get("ts") or 0) >= cutoff]
      if not edge["recent"]:
        edge.pop("recent", None)
    if edge["count"] <= 0:
      state.route_history_edges.pop(key, None)
      removed.append(key)
    else:
      updated[key] = edge
    state.route_history_compact = True

  return list(updated.values()), removed


def _append_route_history_file(entries: List[Dict[str, Any]]) -> None:
  if not ROUTE_HISTORY_ENABLED or not ROUTE_HISTORY_FILE:
    return
  if not entries:
    return
  try:
    os.makedirs(os.path.dirname(ROUTE_HISTORY_FILE), exist_ok=True)
    with open(ROUTE_HISTORY_FILE, "a", encoding="utf-8") as handle:
      for entry in entries:
        handle.write(json.dumps(entry) + "\n")
  except Exception as exc:
    print(f"[history] failed to append {ROUTE_HISTORY_FILE}: {exc}")


def _load_route_history() -> None:
  if not ROUTE_HISTORY_ENABLED or not ROUTE_HISTORY_FILE:
    return
  if not os.path.exists(ROUTE_HISTORY_FILE):
    return

  cutoff = time.time() - (ROUTE_HISTORY_HOURS * 3600)
  loaded_any = False

  try:
    with open(ROUTE_HISTORY_FILE, "r", encoding="utf-8") as handle:
      for line in handle:
        line = line.strip()
        if not line:
          continue
        try:
          entry = json.loads(line)
        except json.JSONDecodeError:
          state.route_history_compact = True
          continue
        if not isinstance(entry, dict):
          state.route_history_compact = True
          continue
        ts = entry.get("ts")
        if not isinstance(ts, (int, float)) or ts < cutoff:
          state.route_history_compact = True
          continue
        a_point = _normalize_history_point(entry.get("a"))
        b_point = _normalize_history_point(entry.get("b"))
        if not a_point or not b_point:
          state.route_history_compact = True
          continue
        sample = {
          "ts": float(ts),
          "message_hash": entry.get("message_hash"),
          "payload_type": entry.get("payload_type"),
          "origin_id": entry.get("origin_id"),
          "receiver_id": entry.get("receiver_id"),
          "route_mode": entry.get("route_mode"),
          "topic": entry.get("topic"),
        }
        key, first, second = _history_edge_key(a_point, b_point)
        state.route_history_segments.append(
          {
            "ts": float(ts),
            "a": [first[0], first[1]],
            "b": [second[0], second[1]],
            "a_id": entry.get("a_id"),
            "b_id": entry.get("b_id"),
            "message_hash": sample.get("message_hash"),
            "payload_type": sample.get("payload_type"),
            "origin_id": sample.get("origin_id"),
            "receiver_id": sample.get("receiver_id"),
            "route_mode": sample.get("route_mode"),
            "topic": sample.get("topic"),
          }
        )
        edge = state.route_history_edges.get(key)
        if not edge:
          edge = {
            "id": key,
            "a": [first[0], first[1]],
            "b": [second[0], second[1]],
            "count": 0,
            "last_ts": float(ts),
          }
          state.route_history_edges[key] = edge
        edge["count"] = int(edge.get("count", 0)) + 1
        edge["last_ts"] = max(edge.get("last_ts", float(ts)), float(ts))
        _update_history_edge_recent(edge, sample)
        loaded_any = True
  except Exception as exc:
    print(f"[history] failed to load {ROUTE_HISTORY_FILE}: {exc}")
    return

  if not loaded_any:
    return

  if ROUTE_HISTORY_MAX_SEGMENTS > 0 and len(
    state.route_history_segments
  ) > ROUTE_HISTORY_MAX_SEGMENTS:
    _prune_route_history(force_limit=True)
    state.route_history_compact = True


async def _route_history_saver() -> None:
  if not ROUTE_HISTORY_ENABLED or not ROUTE_HISTORY_FILE:
    return
  while True:
    await asyncio.sleep(max(5.0, ROUTE_HISTORY_COMPACT_INTERVAL))
    if not state.route_history_compact:
      continue
    now = time.time()
    if now - state.route_history_last_compact < ROUTE_HISTORY_COMPACT_INTERVAL:
      continue
    try:
      os.makedirs(os.path.dirname(ROUTE_HISTORY_FILE), exist_ok=True)
      tmp_path = f"{ROUTE_HISTORY_FILE}.tmp"
      with open(tmp_path, "w", encoding="utf-8") as handle:
        for entry in state.route_history_segments:
          if not isinstance(entry, dict):
            continue
          handle.write(json.dumps(entry) + "\n")
      os.replace(tmp_path, ROUTE_HISTORY_FILE)
      state.route_history_last_compact = now
      state.route_history_compact = False
    except Exception as exc:
      print(f"[history] failed to compact {ROUTE_HISTORY_FILE}: {exc}")
