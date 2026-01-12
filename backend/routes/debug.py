"""
Debug routes for development/troubleshooting.
"""

import time

from fastapi import APIRouter, HTTPException

from config import (
  DECODE_WITH_NODE,
  DIRECT_COORDS_ALLOW_ZERO,
  DIRECT_COORDS_MODE,
  DIRECT_COORDS_TOPIC_REGEX,
  PROD_MODE,
)
from decoder import (
  DIRECT_COORDS_TOPIC_RE,
  ROUTE_PAYLOAD_TYPES_SET,
  _node_ready_once,
  _node_unavailable_once,
)
from state import (
  debug_last,
  devices,
  result_counts,
  route_history_edges,
  route_history_segments,
  routes,
  seen_devices,
  stats,
  status_last,
  topic_counts,
)

router = APIRouter()


@router.get("/stats")
def get_stats():
  """Return message statistics and counters."""
  if PROD_MODE:
    return {
      "stats": {
        "received_total": stats.get("received_total"),
        "parsed_total": stats.get("parsed_total"),
        "unparsed_total": stats.get("unparsed_total"),
        "last_rx_ts": stats.get("last_rx_ts"),
        "last_parsed_ts": stats.get("last_parsed_ts"),
      },
      "result_counts": result_counts,
      "mapped_devices": len(devices),
      "route_count": len(routes),
      "history_edge_count": len(route_history_edges),
      "seen_devices": len(seen_devices),
      "server_time": time.time(),
    }

  top_topics = sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]
  return {
    "stats": stats,
    "result_counts": result_counts,
    "mapped_devices": len(devices),
    "route_count": len(routes),
    "history_edge_count": len(route_history_edges),
    "history_segments": len(route_history_segments),
    "seen_devices": len(seen_devices),
    "seen_recent": sorted(seen_devices.items(), key=lambda kv: kv[1], reverse=True)[:20],
    "top_topics": top_topics,
    "decoder": {
      "decode_with_node": DECODE_WITH_NODE,
      "node_ready": _node_ready_once,
      "node_unavailable": _node_unavailable_once,
    },
    "route_payload_types": sorted(ROUTE_PAYLOAD_TYPES_SET),
    "direct_coords": {
      "mode": DIRECT_COORDS_MODE,
      "topic_regex": DIRECT_COORDS_TOPIC_REGEX,
      "regex_valid": DIRECT_COORDS_TOPIC_RE is not None,
      "allow_zero": DIRECT_COORDS_ALLOW_ZERO,
    },
    "server_time": time.time(),
  }


@router.get("/debug/last")
def debug_last_entries():
  """Return recent MQTT message debug entries."""
  if PROD_MODE:
    raise HTTPException(status_code=404, detail="not_found")
  return {
    "count": len(debug_last),
    "items": list(reversed(list(debug_last))),
    "server_time": time.time(),
  }


@router.get("/debug/status")
def debug_status_entries():
  """Return recent status message debug entries."""
  if PROD_MODE:
    raise HTTPException(status_code=404, detail="not_found")
  return {
    "count": len(status_last),
    "items": list(reversed(list(status_last))),
    "server_time": time.time(),
  }
