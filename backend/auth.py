"""
Authentication helpers for API routes.
"""

from typing import Dict, Optional

from fastapi import HTTPException, Request, WebSocket

from config import PROD_MODE, PROD_TOKEN


def extract_token(headers: Dict[str, str]) -> Optional[str]:
  """Extract bearer token from request headers."""
  auth = headers.get("authorization")
  if auth:
    parts = auth.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
      return parts[1]
    return auth.strip()
  return headers.get("x-access-token") or headers.get("x-token")


def require_prod_token(request: Request) -> None:
  """Raise HTTPException if production token is missing or invalid."""
  if not PROD_MODE:
    return
  if not PROD_TOKEN:
    raise HTTPException(status_code=503, detail="prod_token_not_set")
  token = request.query_params.get("token") or request.query_params.get("access_token")
  if not token:
    token = extract_token(request.headers)
  if token != PROD_TOKEN:
    raise HTTPException(status_code=401, detail="unauthorized")


def ws_authorized(ws: WebSocket) -> bool:
  """Check if WebSocket connection is authorized."""
  if not PROD_MODE:
    return True
  if not PROD_TOKEN:
    return False
  token = ws.query_params.get("token") or ws.query_params.get("access_token")
  if not token:
    token = extract_token(ws.headers)
  return token == PROD_TOKEN
