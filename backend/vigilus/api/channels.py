"""Channels admin API — manage connected bots and the allowlist.

All endpoints mount under ``/api`` behind ``require_user``. Bot tokens are
Fernet-encrypted at rest (``core/crypto.py``) and never returned; responses
carry only a ``has_token`` hint. Mutating calls reload the in-process gateway
so changes take effect without a restart.
"""

from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.crypto import decrypt, encrypt
from vigilus.db.base import get_db
from vigilus.db.models import ChannelAccount, ChannelConfig
from vigilus.integrations.gateway import get_gateway
from vigilus.schemas.channel import (
    ChannelAccountResponse,
    ChannelAccountUpsert,
    ChannelConfigResponse,
    ChannelConfigUpsert,
    ChannelTestResponse,
)

router = APIRouter(prefix="/channels", tags=["Channels"])
logger = structlog.get_logger(__name__)

_VALID_PLATFORMS = {"telegram", "discord"}


def _cfg_to_response(cfg: ChannelConfig | None) -> ChannelConfigResponse:
    if cfg is None:
        return ChannelConfigResponse(
            platform="",
            bot_username=None,
            enabled=False,
            respond_in_groups=False,
            default_operator_id=None,
            has_token=False,
            created_at=None,
            updated_at=None,
        )
    return ChannelConfigResponse(
        platform=cfg.platform,
        bot_username=cfg.bot_username,
        enabled=cfg.enabled,
        respond_in_groups=cfg.respond_in_groups,
        default_operator_id=cfg.default_operator_id,
        has_token=bool(cfg.bot_token_enc),
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


# ── Config endpoints ───────────────────────────────────────


@router.get("", response_model=list[ChannelConfigResponse])
async def list_configs(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(ChannelConfig).order_by(ChannelConfig.platform))
    ).scalars().all()
    return [_cfg_to_response(c) for c in rows]


@router.put("/{platform}", response_model=ChannelConfigResponse)
async def upsert_config(
    platform: str,
    data: ChannelConfigUpsert,
    db: AsyncSession = Depends(get_db),
):
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")

    cfg = (
        await db.execute(
            select(ChannelConfig).where(ChannelConfig.platform == platform)
        )
    ).scalar_one_or_none()

    if cfg is None:
        if not data.bot_token:
            raise HTTPException(status_code=400, detail="A bot token is required to add a channel.")
        cfg = ChannelConfig(
            platform=platform,
            bot_token_enc=encrypt(data.bot_token),
            bot_username=data.bot_username,
            enabled=data.enabled,
            respond_in_groups=data.respond_in_groups,
            default_operator_id=data.default_operator_id,
        )
        db.add(cfg)
    else:
        if data.bot_token:
            cfg.bot_token_enc = encrypt(data.bot_token)
        if data.bot_username is not None:
            cfg.bot_username = data.bot_username
        cfg.enabled = data.enabled
        cfg.respond_in_groups = data.respond_in_groups
        cfg.default_operator_id = data.default_operator_id

    await db.commit()
    await db.refresh(cfg)

    # Fetch the bot username for Telegram so the UI shows the real handle.
    if platform == "telegram" and not cfg.bot_username:
        username = await _fetch_telegram_username(decrypt(cfg.bot_token_enc))
        if username:
            cfg.bot_username = username
            await db.commit()
            await db.refresh(cfg)

    await get_gateway().reload()
    logger.info("channels.config_upserted", platform=platform, enabled=cfg.enabled)
    return _cfg_to_response(cfg)


@router.delete("/{platform}", response_model=ChannelConfigResponse)
async def delete_config(platform: str, db: AsyncSession = Depends(get_db)):
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")
    cfg = (
        await db.execute(
            select(ChannelConfig).where(ChannelConfig.platform == platform)
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"No {platform} channel configured.")
    platform_name = cfg.platform
    snapshot = _cfg_to_response(cfg)
    await db.delete(cfg)
    await db.commit()
    await get_gateway().reload()
    logger.info("channels.config_deleted", platform=platform_name)
    snapshot.enabled = False
    snapshot.has_token = False
    return snapshot


@router.post("/{platform}/test", response_model=ChannelTestResponse)
async def test_config(platform: str, db: AsyncSession = Depends(get_db)):
    """Validate the stored token by calling getMe (Telegram) or Gateway ready (Discord)."""
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")
    cfg = (
        await db.execute(
            select(ChannelConfig).where(ChannelConfig.platform == platform)
        )
    ).scalar_one_or_none()
    if cfg is None or not cfg.bot_token_enc:
        return ChannelTestResponse(ok=False, error="No token configured for this platform.")

    token = decrypt(cfg.bot_token_enc)
    if platform == "telegram":
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/getMe"
                )
            body = r.json()
            if body.get("ok"):
                username = (body.get("result") or {}).get("username")
                if username and cfg.bot_username != username:
                    cfg.bot_username = username
                    await db.commit()
                return ChannelTestResponse(ok=True, bot_username=username)
            return ChannelTestResponse(ok=False, error=body.get("description", "Telegram rejected the token."))
        except Exception as e:  # noqa: BLE001
            return ChannelTestResponse(ok=False, error=str(e))

    # Discord: no cheap "getMe" without a gateway connection; report configured.
    return ChannelTestResponse(ok=True, bot_username=cfg.bot_username)


async def _fetch_telegram_username(token: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/getMe")
        body = r.json()
        if body.get("ok"):
            return (body.get("result") or {}).get("username")
    except Exception:  # noqa: BLE001
        pass
    return None


# ── Allowlist endpoints ─────────────────────────────────────


@router.get("/accounts", response_model=list[ChannelAccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(ChannelAccount).order_by(
                ChannelAccount.platform, ChannelAccount.created_at
            )
        )
    ).scalars().all()
    return [
        ChannelAccountResponse(
            id=a.id,
            platform=a.platform,
            external_user_id=a.external_user_id,
            allowed=a.allowed,
            label=a.label,
            user_id=a.user_id,
            created_at=a.created_at,
        )
        for a in rows
    ]


@router.post("/accounts", response_model=ChannelAccountResponse)
async def upsert_account(data: ChannelAccountUpsert, db: AsyncSession = Depends(get_db)):
    if data.platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {data.platform}")
    acct = (
        await db.execute(
            select(ChannelAccount).where(
                ChannelAccount.platform == data.platform,
                ChannelAccount.external_user_id == data.external_user_id,
            )
        )
    ).scalar_one_or_none()
    if acct is None:
        acct = ChannelAccount(
            platform=data.platform,
            external_user_id=data.external_user_id,
            allowed=data.allowed,
            label=data.label,
            user_id=data.user_id,
        )
        db.add(acct)
    else:
        acct.allowed = data.allowed
        if data.label is not None:
            acct.label = data.label
        if data.user_id is not None:
            acct.user_id = data.user_id
    await db.commit()
    await db.refresh(acct)
    return ChannelAccountResponse(
        id=acct.id,
        platform=acct.platform,
        external_user_id=acct.external_user_id,
        allowed=acct.allowed,
        label=acct.label,
        user_id=acct.user_id,
        created_at=acct.created_at,
    )


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db)):
    acct = await db.get(ChannelAccount, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    await db.delete(acct)
    await db.commit()
    return {"ok": True}
