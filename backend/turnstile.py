import time
import secrets
import json
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

import httpx


@dataclass
class TokenData:
  """Represents a verified Turnstile token."""
  token: str
  created_at: float
  expires_at: float


class TurnstileVerifier:
  """Handles Cloudflare Turnstile token verification and management."""

  def __init__(
    self,
    secret_key: str,
    api_url: str,
    token_ttl_seconds: int,
  ):
    """Initialize the Turnstile verifier.
    
    Args:
      secret_key: Cloudflare Turnstile secret key
      api_url: Cloudflare Turnstile API verification URL
      token_ttl_seconds: Token time-to-live in seconds
    """
    self.secret_key = secret_key
    self.api_url = api_url
    self.token_ttl_seconds = token_ttl_seconds
    self.issued_tokens: Dict[str, TokenData] = {}

  async def verify_turnstile_token(
    self, token: str, remote_ip: Optional[str] = None
  ) -> Tuple[bool, Optional[str]]:
    """Verify a Turnstile token with Cloudflare API.
    
    Args:
      token: The token from Turnstile widget
      remote_ip: Optional client IP address for verification
      
    Returns:
      Tuple of (success: bool, error: Optional[str])
    """
    try:
      async with httpx.AsyncClient() as client:
        response = await client.post(
          self.api_url,
          data={
            "secret": self.secret_key,
            "response": token,
          },
          timeout=10.0,
        )
        result = response.json()

      if result.get("success"):
        return True, None
      else:
        error_codes = result.get("error-codes", [])
        return False, f"Verification failed: {error_codes}"

    except Exception as e:
      return False, f"Verification error: {str(e)}"

  def issue_auth_token(self) -> str:
    """Issue a new authentication token.
    
    Returns:
      A new auth token string
    """
    token = secrets.token_urlsafe(32)
    now = time.time()
    self.issued_tokens[token] = TokenData(
      token=token,
      created_at=now,
      expires_at=now + self.token_ttl_seconds,
    )
    return token

  def verify_auth_token(self, token: str) -> bool:
    """Verify if an issued authentication token is valid.
    
    Args:
      token: The auth token to verify
      
    Returns:
      True if token is valid and not expired, False otherwise
    """
    if token not in self.issued_tokens:
      return False

    token_data = self.issued_tokens[token]
    if time.time() > token_data.expires_at:
      del self.issued_tokens[token]
      return False

    return True

  def cleanup_expired_tokens(self) -> None:
    """Remove expired tokens from storage."""
    now = time.time()
    expired = [t for t, d in self.issued_tokens.items() if d.expires_at < now]
    for token in expired:
      del self.issued_tokens[token]
