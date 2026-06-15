"""Pydantic v2 schemas for auth endpoints."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{3,64}$")


class SetupRequest(BaseModel):
    username: str
    password: str = Field(..., min_length=10)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        if not _USERNAME_RE.match(v):
            raise ValueError(
                "Username must be 3–64 characters and contain only letters, "
                "digits, underscores, dots, or hyphens."
            )
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=10)


class TokenResponse(BaseModel):
    """Bearer token for non-browser clients (TUI, scripts)."""

    token: str
    expires_at: datetime
    username: str


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    created_at: datetime
    last_login_at: datetime | None = None
