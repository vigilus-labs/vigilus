"""RBAC primitives – Permission ordering, PolicyEngine, and JIT tokens."""

from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from vigilus.config import get_settings
from vigilus.db.models import Operator

logger = structlog.get_logger(__name__)


class Permission(str, enum.Enum):
    """Permission levels with implicit ordering (read < write < exec < elevate)."""

    read = "read"
    write = "write"
    exec = "exec"
    elevate = "elevate"

    @property
    def level(self) -> int:
        return _PERMISSION_ORDER[self]

    def __ge__(self, other: Permission) -> bool:  # type: ignore[override]
        return self.level >= other.level

    def __gt__(self, other: Permission) -> bool:
        return self.level > other.level

    def __le__(self, other: Permission) -> bool:  # type: ignore[override]
        return self.level <= other.level

    def __lt__(self, other: Permission) -> bool:
        return self.level < other.level


_PERMISSION_ORDER: dict[Permission, int] = {
    Permission.read: 0,
    Permission.write: 1,
    Permission.exec: 2,
    Permission.elevate: 3,
}


@dataclass
class JITToken:
    """A time-limited Just-In-Time elevation token."""

    token_id: str
    operator_id: str
    resource: str
    permission: Permission
    granted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ttl_minutes: int = 15
    revoked: bool = False

    @property
    def expires_at(self) -> datetime:
        return self.granted_at + timedelta(minutes=self.ttl_minutes)

    @property
    def is_valid(self) -> bool:
        if self.revoked:
            return False
        return datetime.now(UTC) < self.expires_at


class WardenService:
    def __init__(self):
        self.settings = get_settings()
        self.secret_key = self.settings.secret_key.encode("utf-8")

    async def request_jit(
        self,
        db,
        operator: Operator,
        resource: str,
        permission: Permission,
        task_description: str,
        ttl_minutes: int | None = None,
    ):
        from vigilus.core.events import get_event_bus
        from vigilus.db.models import JitRequest, JitStatus, TrustMode

        if ttl_minutes is None:
            ttl_minutes = self.settings.jit_default_ttl_minutes

        # Check trust mode (if inherit, resolve to strict by default for now unless configured)
        mode = operator.trust_mode
        if mode == TrustMode.inherit:
            # Simplified for now: map inherit to strict
            mode = TrustMode.strict

        status = JitStatus.approved if mode == TrustMode.lenient else JitStatus.pending

        req = JitRequest(
            operator_id=operator.id,
            resource=resource,
            permission=permission.name,  # Use string enum
            task_description=task_description,
            ttl_minutes=ttl_minutes,
            status=status,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)

        token = None
        if status == JitStatus.approved:
            token = self.issue_token(operator.id, resource, permission, ttl_minutes)
            req.token_id = token
            req.resolved_at = datetime.now(UTC)
            req.approved_by = "auto (lenient)"
            await db.commit()

        await get_event_bus().publish(
            "jit.requested" if status == JitStatus.pending else "jit.resolved",
            {
                "id": req.id,
                "operator_id": operator.id,
                "operator_name": operator.name,
                "resource": resource,
                "permission": permission.value,
                "status": req.status.value,
                "task_description": task_description,
            },
        )
        return req, token

    async def approve_request(
        self,
        db,
        request_id: str,
        approver: str = "admin",
        *,
        ttl_minutes: int | None = None,
        single_use: bool = False,
        resource: str | None = None,
    ) -> str:
        """Approve a pending JIT request and issue a token.

        The approver controls the grant's blast radius:
          - ``single_use=True`` → "once": authorizes only the command that asked
            (excluded from the reuse lookup, so the next call re-prompts).
          - ``ttl_minutes`` → how long a "timed" grant stays reusable (clamped to
            ``jit_max_ttl_minutes``; defaults to the request's own TTL).
          - ``resource`` → optionally broaden ("*") or narrow what the grant
            covers; defaults to the resource the request was raised for.
        """
        from vigilus.core.events import get_event_bus
        from vigilus.db.models import JitRequest, JitStatus

        req = await db.get(JitRequest, request_id)
        if not req or req.status != JitStatus.pending:
            raise ValueError("Invalid request")

        if ttl_minutes is None:
            ttl_minutes = req.ttl_minutes
        ttl_minutes = max(1, min(ttl_minutes, self.settings.jit_max_ttl_minutes))
        granted_resource = resource if resource else req.resource

        token = self.issue_token(
            req.operator_id, granted_resource, Permission(req.permission.value), ttl_minutes
        )
        req.status = JitStatus.approved
        req.token_id = token
        req.ttl_minutes = ttl_minutes
        req.scope_mode = "once" if single_use else "timed"
        req.resource = granted_resource
        req.resolved_at = datetime.now(UTC)
        req.approved_by = approver
        await db.commit()

        await get_event_bus().publish(
            "jit.resolved",
            {"id": req.id, "operator_id": req.operator_id, "status": req.status.value},
        )
        return token

    async def deny_request(self, db, request_id: str, approver: str = "admin"):
        from vigilus.core.events import get_event_bus
        from vigilus.db.models import JitRequest, JitStatus

        req = await db.get(JitRequest, request_id)
        if not req or req.status != JitStatus.pending:
            raise ValueError("Invalid request")

        req.status = JitStatus.denied
        req.resolved_at = datetime.now(UTC)
        req.approved_by = approver
        await db.commit()

        await get_event_bus().publish(
            "jit.resolved",
            {"id": req.id, "operator_id": req.operator_id, "status": req.status.value},
        )

    def issue_token(
        self, operator_id: str, resource: str, permission: Permission, ttl_minutes: int = 15
    ) -> str:
        granted_at = datetime.now(UTC)
        payload = {
            "operator_id": operator_id,
            "resource": resource,
            "permission": permission.value,
            "granted_at": granted_at.isoformat(),
            "ttl_minutes": ttl_minutes,
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")

        signature = hmac.new(self.secret_key, payload_bytes, hashlib.sha256).digest()
        signature_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

        return f"{payload_b64}.{signature_b64}"

    def validate_token(self, token: str) -> JITToken | None:
        parts = token.split(".")
        if len(parts) != 2:
            return None

        payload_b64, signature_b64 = parts

        # Add padding back
        payload_padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        try:
            payload_bytes = base64.urlsafe_b64decode(payload_padded)
        except Exception:
            return None

        expected_signature = hmac.new(self.secret_key, payload_bytes, hashlib.sha256).digest()
        expected_signature_b64 = (
            base64.urlsafe_b64encode(expected_signature).decode("utf-8").rstrip("=")
        )

        if not hmac.compare_digest(signature_b64, expected_signature_b64):
            return None

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
            granted_at = datetime.fromisoformat(payload["granted_at"])
            jit = JITToken(
                token_id=token,
                operator_id=payload["operator_id"],
                resource=payload["resource"],
                permission=Permission(payload["permission"]),
                granted_at=granted_at,
                ttl_minutes=payload["ttl_minutes"],
                revoked=False,
            )
            return jit if jit.is_valid else None
        except Exception:
            return None


def _resource_covers(token_resource: str, requested: str) -> bool:
    """True if a JIT token's resource scope covers the requested resource.

    Covers exact matches, glob patterns ("/etc/nginx/*"), the universal
    wildcard "*", and path containment (a token for "/etc/nginx" covers
    "/etc/nginx/nginx.conf").
    """
    from fnmatch import fnmatch

    if token_resource == "*" or requested == token_resource:
        return True
    if fnmatch(requested, token_resource):
        return True
    try:
        return os.path.commonpath([token_resource, requested]) == os.path.normpath(token_resource)
    except ValueError:
        return False


class PolicyEngine:
    """Evaluates whether an operator is allowed to invoke a tool."""

    def __init__(self):
        self.warden = WardenService()

    async def check_permission(
        self,
        operator: Any,
        tool_name: str,
        *,
        required_permission: Permission = Permission.read,
        jit_token: JITToken | None = None,
        resource_path: str | None = None,
    ) -> bool:
        """Check whether the operator has permission for the requested tool."""
        logger.info(
            "rbac.check_permission",
            operator_id=operator.id,
            tool_name=tool_name,
            required=required_permission.value,
        )

        # Rule 2: If a valid jit_token covers this operator, permission level,
        # AND resource. Tokens issued for "*" cover any resource; otherwise the
        # requested resource must match the token's resource (glob patterns ok).
        if jit_token and jit_token.is_valid:
            if jit_token.operator_id == operator.id and jit_token.permission >= required_permission:
                requested = resource_path or "*"
                if _resource_covers(jit_token.resource, requested):
                    return True
                logger.info(
                    "rbac.jit_resource_mismatch",
                    operator_id=operator.id,
                    token_resource=jit_token.resource,
                    requested=requested,
                )

        # Rule 1: check base permission
        has_base_perm = Permission(operator.permission_level.value) >= required_permission

        # If accessing host file system, check working_dir boundary.
        # Only meaningful for absolute paths — relative paths and pseudo
        # resources ("*") are resolved/confined by the tool handlers.
        if (
            has_base_perm
            and resource_path
            and operator.working_dir
            and os.path.isabs(resource_path)
        ):
            try:
                abs_resource = os.path.realpath(resource_path)
                abs_working_dir = os.path.realpath(operator.working_dir)
                # commonpath (not startswith) so /data does not authorize /data-evil
                if os.path.commonpath([abs_working_dir, abs_resource]) != abs_working_dir:
                    logger.info(
                        "rbac.deny_outside_working_dir",
                        operator_id=operator.id,
                        resource=resource_path,
                        working_dir=operator.working_dir,
                    )
                    return False
            except Exception:
                return False

        if has_base_perm:
            return True

        logger.info(
            "rbac.deny_insufficient_permission",
            operator_id=operator.id,
            req=required_permission,
            has=operator.permission_level.value,
        )
        return False
