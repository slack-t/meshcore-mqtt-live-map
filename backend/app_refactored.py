"""
Mesh Live Map - FastAPI Application

This is the refactored entry point that imports modular components.
The original app.py is preserved for reference during the transition.
"""

import asyncio

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from decoder import _ensure_node_decoder
from history import _load_route_history, _route_history_saver
from routes.api import router as api_router
from routes.debug import router as debug_router
from routes.static import router as static_router
from routes.websocket import router as ws_router
from services.broadcaster import broadcaster, check_git_updates, git_check_loop
from services.mqtt import create_client, stop_client
from services.persistence import load_state, state_saver
from services.reaper import reaper

# =========================
# App Setup
# =========================
app = FastAPI(title="Mesh Live Map", version="1.0.2")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(static_router)
app.include_router(api_router)
app.include_router(debug_router)
app.include_router(ws_router)


# =========================
# Startup / Shutdown
# =========================
@app.on_event("startup")
async def startup():
  """Initialize services on startup."""
  # Load persisted state
  load_state()
  _load_route_history()

  # Initialize decoder
  _ensure_node_decoder()

  # Check for updates
  check_git_updates()

  # Start MQTT client
  loop = asyncio.get_event_loop()
  create_client(loop)

  # Start background tasks
  asyncio.create_task(broadcaster())
  asyncio.create_task(reaper())
  asyncio.create_task(state_saver())
  asyncio.create_task(_route_history_saver())
  asyncio.create_task(git_check_loop())


@app.on_event("shutdown")
async def shutdown():
  """Clean up on shutdown."""
  stop_client()
