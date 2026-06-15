"""Auth routes: setup, login, logout, me, change-password."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.api.deps import require_user
from vigilus.config import get_settings
from vigilus.core.auth import (
    create_token,
    dummy_verify,
    hash_password,
    login_limiter,
    verify_password,
)
from vigilus.db.base import get_db
from vigilus.db.models import User
from vigilus.schemas.auth import (
    AuthUserResponse,
    ChangePasswordRequest,
    LoginRequest,
    SetupRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = structlog.get_logger("vigilus.auth")


def _set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
        max_age=settings.auth_token_ttl_hours * 3600,
        path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ── GET /auth/setup ───────────────────────────────────────────────────────────

@router.get("/setup")
async def get_setup_status(db: AsyncSession = Depends(get_db)):
    count = await db.scalar(select(func.count()).select_from(User))
    return {"needs_setup": count == 0}


# ── POST /auth/setup ──────────────────────────────────────────────────────────

@router.post("/setup", response_model=AuthUserResponse)
async def setup_first_user(
    data: SetupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    count = await db.scalar(select(func.count()).select_from(User))
    if count > 0:
        raise HTTPException(status_code=409, detail="Setup already complete.")

    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_token(user.id, user.token_version)
    _set_auth_cookie(response, token)
    logger.info("auth.setup_complete", username=user.username)
    return AuthUserResponse.model_validate(user)


# ── POST /auth/login ──────────────────────────────────────────────────────────

async def _authenticate(data: LoginRequest, request: Request, db: AsyncSession) -> User:
    """Verify credentials with rate limiting; raises HTTPException on failure."""
    ip = _client_ip(request)

    if not login_limiter.check(data.username, ip):
        logger.warning("auth.login_locked", username=data.username, ip=ip)
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
        )

    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()

    if user is None:
        dummy_verify()
        login_limiter.record_failure(data.username, ip)
        logger.warning("auth.login_failed", username=data.username, ip=ip, reason="no_user")
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    if not verify_password(data.password, user.password_hash):
        login_limiter.record_failure(data.username, ip)
        logger.warning("auth.login_failed", username=data.username, ip=ip, reason="bad_password")
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    login_limiter.record_success(data.username, ip)
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=AuthUserResponse)
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    user = await _authenticate(data, request, db)
    token = create_token(user.id, user.token_version)
    _set_auth_cookie(response, token)
    logger.info("auth.login_ok", username=user.username, ip=_client_ip(request))
    return AuthUserResponse.model_validate(user)


# ── POST /auth/token ──────────────────────────────────────────────────────────

@router.post("/token", response_model=TokenResponse)
async def issue_token(
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Issue a bearer token for non-browser clients (the TUI).

    Same JWT as the cookie session — accepted via ``Authorization: Bearer``.
    Password changes invalidate it through the usual token_version bump.
    """
    user = await _authenticate(data, request, db)
    token = create_token(user.id, user.token_version)
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.auth_token_ttl_hours)
    logger.info("auth.token_issued", username=user.username, ip=_client_ip(request))
    return TokenResponse(token=token, expires_at=expires_at, username=user.username)


# ── POST /auth/logout ─────────────────────────────────────────────────────────

@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(require_user),
):
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        samesite="lax",
    )
    logger.info("auth.logout", username=user.username)


# ── GET /auth/me ──────────────────────────────────────────────────────────────

@router.get("/me", response_model=AuthUserResponse)
async def get_me(user: User = Depends(require_user)):
    return AuthUserResponse.model_validate(user)


# ── POST /auth/change-password ────────────────────────────────────────────────

@router.post("/change-password", status_code=204)
async def change_password(
    data: ChangePasswordRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    user.password_hash = hash_password(data.new_password)
    user.token_version += 1
    await db.commit()
    await db.refresh(user)

    # Re-issue cookie with new version so current session survives
    token = create_token(user.id, user.token_version)
    _set_auth_cookie(response, token)
    logger.info("auth.password_changed", username=user.username)
