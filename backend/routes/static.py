"""
Static file and root HTML routes.
"""

import html
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from config import (
  APP_DIR,
  COVERAGE_API_URL,
  CUSTOM_LINK_URL,
  DISTANCE_UNITS,
  HISTORY_LINK_SCALE,
  LOS_ELEVATION_URL,
  LOS_PEAKS_MAX,
  LOS_SAMPLE_MAX,
  LOS_SAMPLE_MIN,
  LOS_SAMPLE_STEP_METERS,
  MAP_DEFAULT_LAYER,
  MAP_RADIUS_KM,
  MAP_RADIUS_SHOW,
  MAP_START_LAT,
  MAP_START_LON,
  MAP_START_ZOOM,
  MQTT_ONLINE_SECONDS,
  NODE_MARKER_RADIUS,
  PROD_MODE,
  PROD_TOKEN,
  SITE_DESCRIPTION,
  SITE_FEED_NOTE,
  SITE_ICON,
  SITE_OG_IMAGE,
  SITE_TITLE,
  SITE_URL,
  TRAIL_LEN,
)

router = APIRouter()


@router.get("/")
def root():
  """Serve the main HTML page with injected configuration."""
  from services.broadcaster import git_update_info

  html_path = os.path.join(APP_DIR, "static", "index.html")
  try:
    with open(html_path, "r", encoding="utf-8") as handle:
      content = handle.read()
  except Exception:
    return FileResponse("static/index.html")

  og_image_tag = ""
  twitter_image_tag = ""
  if SITE_OG_IMAGE:
    safe_image = html.escape(str(SITE_OG_IMAGE), quote=True)
    og_image_tag = f'<meta property="og:image" content="{safe_image}" />'
    twitter_image_tag = f'<meta name="twitter:image" content="{safe_image}" />'

  content = content.replace("{{OG_IMAGE_TAG}}", og_image_tag)
  content = content.replace("{{TWITTER_IMAGE_TAG}}", twitter_image_tag)

  trail_info_suffix = ""
  if TRAIL_LEN > 0:
    trail_info_suffix = f" Trails show last ~{TRAIL_LEN} points."

  replacements = {
    "SITE_TITLE": SITE_TITLE,
    "SITE_DESCRIPTION": SITE_DESCRIPTION,
    "SITE_URL": SITE_URL,
    "SITE_ICON": SITE_ICON,
    "SITE_FEED_NOTE": SITE_FEED_NOTE,
    "CUSTOM_LINK_URL": CUSTOM_LINK_URL,
    "DISTANCE_UNITS": DISTANCE_UNITS,
    "NODE_MARKER_RADIUS": NODE_MARKER_RADIUS,
    "HISTORY_LINK_SCALE": HISTORY_LINK_SCALE,
    "TRAIL_INFO_SUFFIX": trail_info_suffix,
    "PROD_MODE": str(PROD_MODE).lower(),
    "PROD_TOKEN": PROD_TOKEN,
    "MAP_START_LAT": MAP_START_LAT,
    "MAP_START_LON": MAP_START_LON,
    "MAP_START_ZOOM": MAP_START_ZOOM,
    "MAP_RADIUS_KM": MAP_RADIUS_KM,
    "MAP_RADIUS_SHOW": str(MAP_RADIUS_SHOW).lower(),
    "MAP_DEFAULT_LAYER": MAP_DEFAULT_LAYER,
    "LOS_ELEVATION_URL": LOS_ELEVATION_URL,
    "LOS_SAMPLE_MIN": LOS_SAMPLE_MIN,
    "LOS_SAMPLE_MAX": LOS_SAMPLE_MAX,
    "LOS_SAMPLE_STEP_METERS": LOS_SAMPLE_STEP_METERS,
    "LOS_PEAKS_MAX": LOS_PEAKS_MAX,
    "MQTT_ONLINE_SECONDS": MQTT_ONLINE_SECONDS,
    "COVERAGE_API_URL": COVERAGE_API_URL,
    "UPDATE_AVAILABLE": str(bool(git_update_info.get("available"))).lower(),
    "UPDATE_LOCAL": git_update_info.get("local_short") or "",
    "UPDATE_REMOTE": git_update_info.get("remote_short") or "",
    "UPDATE_BANNER_HIDDEN": "" if git_update_info.get("available") else "hidden",
  }
  for key, value in replacements.items():
    safe_value = html.escape(str(value), quote=True)
    content = content.replace(f"{{{{{key}}}}}", safe_value)

  return HTMLResponse(content)


@router.get("/manifest.webmanifest")
def manifest():
  """Serve PWA manifest."""
  icons = []
  if SITE_ICON:
    icons = [
      {
        "src": SITE_ICON,
        "sizes": "192x192",
        "type": "image/png",
        "purpose": "any",
      },
      {
        "src": SITE_ICON,
        "sizes": "512x512",
        "type": "image/png",
        "purpose": "any maskable",
      },
    ]
  short_name = SITE_TITLE if len(SITE_TITLE) <= 12 else SITE_TITLE[:12]
  return JSONResponse(
    {
      "name": SITE_TITLE,
      "short_name": short_name,
      "description": SITE_DESCRIPTION,
      "start_url": "/",
      "scope": "/",
      "display": "standalone",
      "display_override": ["standalone", "minimal-ui"],
      "background_color": "#0f172a",
      "theme_color": "#0f172a",
      "icons": icons,
    },
    media_type="application/manifest+json",
  )


@router.get("/sw.js")
def service_worker():
  """Serve service worker JavaScript."""
  return FileResponse("static/sw.js", media_type="application/javascript")
