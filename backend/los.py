import json
import math
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from config import (
  ELEVATION_CACHE_TTL,
  LOS_ELEVATION_URL,
  LOS_PEAKS_MAX,
  LOS_SAMPLE_MAX,
  LOS_SAMPLE_MIN,
  LOS_SAMPLE_STEP_METERS,
)
from state import elevation_cache


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  radius = 6371000.0
  phi1 = math.radians(lat1)
  phi2 = math.radians(lat2)
  dphi = math.radians(lat2 - lat1)
  dlambda = math.radians(lon2 - lon1)
  a = math.sin(dphi / 2
              )**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
  c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
  return radius * c


def _elevation_cache_key(lat: float, lon: float) -> str:
  return f"{lat:.5f},{lon:.5f}"


def _chunked(seq: List[Any], size: int) -> List[List[Any]]:
  return [seq[i:i + size] for i in range(0, len(seq), size)]


def _fetch_elevations(
  points: List[Tuple[float, float, float]]
) -> Tuple[Optional[List[float]], Optional[str]]:
  now = time.time()
  results: List[Optional[float]] = [None] * len(points)
  missing: List[Tuple[int, float, float, str]] = []

  for idx, (lat, lon, _) in enumerate(points):
    key = _elevation_cache_key(lat, lon)
    cached = elevation_cache.get(key)
    if cached and now - cached[1] <= ELEVATION_CACHE_TTL:
      results[idx] = cached[0]
    else:
      missing.append((idx, lat, lon, key))

  if not missing:
    if any(val is None for val in results):
      return None, "elevation_fetch_failed: incomplete_cache"
    return [float(val) for val in results], None

  for chunk in _chunked(missing, 100):
    locations = "|".join(f"{lat},{lon}" for _, lat, lon, _ in chunk)
    query = urllib.parse.urlencode({"locations": locations})
    url = f"{LOS_ELEVATION_URL}?{query}"
    try:
      with urllib.request.urlopen(url, timeout=6) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
      return None, f"elevation_fetch_failed: {exc}"

    if payload.get("status") not in (None, "OK"):
      return None, f"elevation_fetch_failed: {payload.get('status')}"

    elev_results = payload.get("results", [])
    if len(elev_results) != len(chunk):
      return None, "elevation_fetch_failed: unexpected_result_length"

    for (idx, _, _, key), entry in zip(chunk, elev_results):
      elev = entry.get("elevation")
      if elev is None:
        return None, "elevation_fetch_failed: missing_elevation"
      elevation_cache[key] = (float(elev), now)
      results[idx] = float(elev)

  if any(val is None for val in results):
    return None, "elevation_fetch_failed: incomplete_results"
  return [float(val) for val in results], None


def _sample_los_points(lat1: float, lon1: float, lat2: float,
                       lon2: float) -> List[Tuple[float, float, float]]:
  distance_m = _haversine_m(lat1, lon1, lat2, lon2)
  if distance_m <= 0:
    return [(lat1, lon1, 0.0), (lat2, lon2, 1.0)]

  samples = int(distance_m / max(1.0, LOS_SAMPLE_STEP_METERS)) + 1
  samples = max(LOS_SAMPLE_MIN, min(LOS_SAMPLE_MAX, samples))
  if samples < 2:
    samples = 2

  points: List[Tuple[float, float, float]] = []
  for i in range(samples):
    t = i / (samples - 1)
    lat = lat1 + (lat2 - lat1) * t
    lon = lon1 + (lon2 - lon1) * t
    points.append((lat, lon, t))
  return points


def _los_max_obstruction(
  points: List[Tuple[float, float, float]], elevations: List[float],
  start_idx: int, end_idx: int
) -> float:
  if end_idx <= start_idx + 1:
    return 0.0
  start_t = points[start_idx][2]
  end_t = points[end_idx][2]
  if end_t <= start_t:
    return 0.0
  start_elev = elevations[start_idx]
  end_elev = elevations[end_idx]
  max_obstruction = 0.0
  for idx in range(start_idx + 1, end_idx):
    frac = (points[idx][2] - start_t) / (end_t - start_t)
    line_elev = start_elev + (end_elev - start_elev) * frac
    clearance = elevations[idx] - line_elev
    if clearance > max_obstruction:
      max_obstruction = clearance
  return max_obstruction


def _find_los_suggestion(
  points: List[Tuple[float, float, float]], elevations: List[float]
) -> Optional[Dict[str, Any]]:
  if len(points) < 3:
    return None
  best_idx = None
  best_score = None
  best_clear = False
  for idx in range(1, len(points) - 1):
    obst_a = _los_max_obstruction(points, elevations, 0, idx)
    obst_b = _los_max_obstruction(points, elevations, idx, len(points) - 1)
    score = max(obst_a, obst_b)
    clear = score <= 0.0
    if clear and not best_clear:
      best_idx = idx
      best_score = score
      best_clear = True
    elif clear and best_clear:
      if elevations[idx] > elevations[best_idx]:
        best_idx = idx
        best_score = score
    elif not best_clear:
      if best_score is None or score < best_score:
        best_idx = idx
        best_score = score
  if best_idx is None:
    return None
  return {
    "lat":
      round(points[best_idx][0], 6),
    "lon":
      round(points[best_idx][1], 6),
    "elevation_m":
      round(float(elevations[best_idx]), 2),
    "clear":
      best_clear,
    "max_obstruction_m":
      round(float(best_score), 2) if best_score is not None else None,
  }


def _find_los_peaks(
  points: List[Tuple[float, float, float]],
  elevations: List[float],
  distance_m: float,
) -> List[Dict[str, Any]]:
  if len(points) < 3:
    return []

  peak_indices = []
  for idx in range(1, len(elevations) - 1):
    elev = elevations[idx]
    if elev >= elevations[idx - 1] and elev >= elevations[idx + 1]:
      peak_indices.append(idx)

  if not peak_indices:
    try:
      peak_indices = [
        max(range(1,
                  len(elevations) - 1), key=lambda i: elevations[i])
      ]
    except ValueError:
      return []

  peak_indices = sorted(
    peak_indices, key=lambda i: elevations[i], reverse=True
  )[:LOS_PEAKS_MAX]
  peak_indices = sorted(peak_indices, key=lambda i: points[i][2])

  peaks = []
  for i, idx in enumerate(peak_indices, start=1):
    t = points[idx][2]
    peaks.append(
      {
        "index": i,
        "lat": round(points[idx][0], 6),
        "lon": round(points[idx][1], 6),
        "elevation_m": round(float(elevations[idx]), 2),
        "distance_m": round(distance_m * t, 2),
      }
    )
  return peaks
