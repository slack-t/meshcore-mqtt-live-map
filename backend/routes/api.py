"""
API routes for node data, peers, LOS, and coverage.
"""

import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from auth import require_prod_token
from config import (
  COVERAGE_API_URL,
  LOS_ELEVATION_URL,
  LOS_PEAKS_MAX,
  ROUTE_HISTORY_HOURS,
)
from decoder import _coords_are_zero, _normalize_lat_lon
from helpers import (
  device_payload,
  history_edge_payload,
  node_api_payload,
  parse_updated_since,
  peer_stats_for_device,
  route_payload,
)
from los import (
  _fetch_elevations,
  _find_los_peaks,
  _find_los_suggestion,
  _haversine_m,
  _los_max_obstruction,
  _sample_los_points,
)
from state import (
  device_names,
  device_roles,
  devices,
  route_history_edges,
  routes,
  seen_devices,
  trails,
)

router = APIRouter()


@router.get("/snapshot")
def snapshot(request: Request):
  """Return full state snapshot."""
  from services.broadcaster import git_update_info
  from decoder import _serialize_heat_events

  require_prod_token(request)
  return {
    "devices": {k: device_payload(k, v) for k, v in devices.items()},
    "trails": trails,
    "routes": [route_payload(r) for r in routes.values()],
    "history_edges": [history_edge_payload(e) for e in route_history_edges.values()],
    "history_window_seconds": int(max(0, ROUTE_HISTORY_HOURS * 3600)),
    "heat": _serialize_heat_events(),
    "update": git_update_info,
    "server_time": time.time(),
  }


@router.get("/api/nodes")
def api_nodes(
  request: Request,
  updated_since: Optional[str] = None,
  mode: Optional[str] = None,
  format: Optional[str] = None,
):
  """Return list of nodes, optionally filtered by update time."""
  require_prod_token(request)
  cutoff = parse_updated_since(updated_since)
  mode_value = (mode or "").strip().lower()
  apply_delta = mode_value in ("delta", "updates", "since")
  format_value = (format or "").strip().lower()
  format_flat = format_value in ("flat", "list", "legacy", "v1")

  nodes: List[Dict[str, Any]] = []
  all_nodes: List[Dict[str, Any]] = []
  max_last_seen = 0.0

  for device_id, state in devices.items():
    payload = node_api_payload(device_id, state)
    last_seen = payload.get("last_seen_ts") or 0
    if float(last_seen) > max_last_seen:
      max_last_seen = float(last_seen)
    all_nodes.append(payload)
    if apply_delta and cutoff is not None and float(last_seen) < cutoff:
      continue
    nodes.append(payload)

  nodes.sort(key=lambda item: item.get("public_key") or "")
  if not apply_delta:
    all_nodes.sort(key=lambda item: item.get("public_key") or "")
    nodes = all_nodes

  response: Dict[str, Any] = {
    "server_time": time.time(),
    "max_last_seen_ts": max_last_seen or None,
    "updated_since_applied": bool(apply_delta and cutoff is not None),
    "updated_since_ignored": bool(updated_since and not apply_delta),
  }
  if format_flat:
    response["data"] = nodes
  else:
    response["data"] = {"nodes": nodes}
  return response


@router.get("/peers/{device_id}")
def get_peers(device_id: str, request: Request, limit: int = 8):
  """Return peer statistics for a device."""
  require_prod_token(request)
  if not device_id:
    raise HTTPException(status_code=400, detail="device_id required")

  limit_value = max(1, min(int(limit or 8), 50))
  payload = peer_stats_for_device(device_id, limit_value)

  state = devices.get(device_id)
  if state and not _coords_are_zero(state.lat, state.lon):
    payload["lat"] = float(state.lat)
    payload["lon"] = float(state.lon)
  payload["name"] = (state.name if state else None) or device_names.get(device_id) or ""
  payload["role"] = (state.role if state else None) or device_roles.get(device_id)
  payload["last_seen_ts"] = seen_devices.get(device_id) or (state.ts if state else None)
  payload["server_time"] = time.time()
  return payload


@router.get("/los")
def line_of_sight(lat1: float, lon1: float, lat2: float, lon2: float, profile: bool = False):
  """Calculate line of sight between two points."""
  include_points = bool(profile)
  start = _normalize_lat_lon(lat1, lon1)
  end = _normalize_lat_lon(lat2, lon2)
  if not start or not end:
    return {"ok": False, "error": "invalid_coords"}

  points = _sample_los_points(start[0], start[1], end[0], end[1])
  elevations, error = _fetch_elevations(points)
  if error:
    return {"ok": False, "error": error}

  distance_m = _haversine_m(start[0], start[1], end[0], end[1])
  if distance_m <= 0:
    return {"ok": False, "error": "zero_distance"}

  start_elev = elevations[0]
  end_elev = elevations[-1]
  max_obstruction = _los_max_obstruction(points, elevations, 0, len(points) - 1)
  max_terrain = max(elevations)
  blocked = max_obstruction > 0.0
  suggestion = _find_los_suggestion(points, elevations) if blocked else None

  profile_samples = []
  if distance_m > 0:
    for (lat, lon, t), elev in zip(points, elevations):
      line_elev = start_elev + (end_elev - start_elev) * t
      profile_samples.append([
        round(distance_m * t, 2),
        round(float(elev), 2),
        round(float(line_elev), 2),
      ])

  peaks = _find_los_peaks(points, elevations, distance_m)

  response = {
    "ok": True,
    "blocked": blocked,
    "max_obstruction_m": round(max_obstruction, 2),
    "distance_m": round(distance_m, 2),
    "distance_km": round(distance_m / 1000.0, 3),
    "distance_mi": round(distance_m / 1609.344, 3),
    "samples": len(points),
    "elevation_m": {
      "start": round(start_elev, 2),
      "end": round(end_elev, 2),
      "max_terrain": round(max_terrain, 2),
    },
    "provider": LOS_ELEVATION_URL,
    "note": "Straight-line LOS using SRTM90m. No curvature/refraction.",
    "suggested": suggestion,
    "profile": profile_samples,
    "peaks": peaks,
  }
  if include_points:
    response["profile_points"] = [
      [round(lat, 6), round(lon, 6), round(t, 4), round(float(elev), 2)]
      for (lat, lon, t), elev in zip(points, elevations)
    ]
  return response


@router.get("/coverage")
async def get_coverage():
  """Proxy coverage data from external API."""
  if not COVERAGE_API_URL:
    raise HTTPException(
      status_code=503,
      detail="coverage_api_not_configured: Set COVERAGE_API_URL in .env (e.g., http://localhost:3000)",
    )
  try:
    url = f"{COVERAGE_API_URL}/get-samples"
    print(f"[coverage] Fetching from {url}")
    async with httpx.AsyncClient(timeout=10.0) as client:
      response = await client.get(url)
      response.raise_for_status()
      data = response.json()
      samples = data.get("keys", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
      print(f"[coverage] Received {len(samples) if isinstance(samples, list) else 'non-list'} items")
      if isinstance(samples, list) and len(samples) > 0:
        print(f"[coverage] Sample item keys: {list(samples[0].keys()) if samples[0] else 'N/A'}")
      return samples
  except httpx.TimeoutException:
    raise HTTPException(status_code=504, detail="coverage_api_timeout")
  except httpx.HTTPStatusError as e:
    raise HTTPException(status_code=502, detail=f"coverage_api_error: HTTP {e.response.status_code}")
  except httpx.HTTPError as e:
    raise HTTPException(status_code=502, detail=f"coverage_api_error: {str(e)}")
  except Exception as e:
    raise HTTPException(status_code=500, detail=f"coverage_fetch_error: {str(e)}")
