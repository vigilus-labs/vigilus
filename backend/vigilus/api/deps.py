"""Shared API dependencies — authentication."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.config import get_settings
from vigilus.core.auth import decode_token
from vigilus.db.base import get_db
from vigilus.db.models import User


def bearer_token(authorization: str | None) -> str | None:
    """Extract the JWT from an ``Authorization: Bearer …`` header value."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def require_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    # Browsers authenticate with the HttpOnly cookie; non-browser clients
    # (the TUI, scripts) send the same JWT as a bearer token instead.
    token = request.cookies.get(get_settings().auth_cookie_name) or bearer_token(
        request.headers.get("authorization")
    )
    payload = decode_token(token) if token else None
    if payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active or user.token_version != payload.get("ver"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
