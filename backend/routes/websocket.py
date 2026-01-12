"""
WebSocket endpoint for real-time updates.
"""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from auth import ws_authorized
from config import ROUTE_HISTORY_HOURS
from decoder import _serialize_heat_events
from helpers import device_payload, history_edge_payload, route_payload
from state import devices, route_history_edges, routes, trails

router = APIRouter()

# Set of connected WebSocket clients (managed by broadcaster service)
clients: set = set()


def get_clients() -> set:
  """Get the set of connected WebSocket clients."""
  return clients


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
  """WebSocket endpoint for real-time updates."""
  from services.broadcaster import git_update_info

  if not ws_authorized(ws):
    await ws.accept()
    await ws.close(code=1008)
    return

  await ws.accept()
  clients.add(ws)

  # Send initial snapshot
  await ws.send_text(
    json.dumps({
      "type": "snapshot",
      "devices": {k: device_payload(k, v) for k, v in devices.items()},
      "trails": trails,
      "routes": [route_payload(r) for r in routes.values()],
      "history_edges": [history_edge_payload(e) for e in route_history_edges.values()],
      "history_window_seconds": int(max(0, ROUTE_HISTORY_HOURS * 3600)),
      "heat": _serialize_heat_events(),
      "update": git_update_info,
    })
  )

  try:
    while True:
      await ws.receive_text()
  except WebSocketDisconnect:
    pass
  except RuntimeError:
    pass
  finally:
    clients.discard(ws)
