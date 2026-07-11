"""Pydantic schemas for the Scope (network attack-surface) API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ScopeOverview(BaseModel):
    """Top-line counts for the Scope stat tiles."""

    model_config = ConfigDict(from_attributes=True)

    managed: int
    discovered_unique: int
    unmanaged: int  # discovered but not matched to inventory
    open_ports: int
    findings: int


class ScopeHostNode(BaseModel):
    """One node on the Scope topology: a managed server, a discovered host, or both merged."""

    model_config = ConfigDict(from_attributes=True)

    id: str  # server id (managed) or discovered host id (scan-only)
    label: str
    ip: str | None = None
    hostname: str | None = None
    os: str | None = None
    status: str | None = None
    origins: list[str] = []  # managed | discovered | monitored
    managed: bool = False
    discovered_host_id: str | None = None
    finding_count: int = 0
    open_port_count: int = 0
    monitored: bool = False  # has wazuh-sourced findings
    segment: str | None = None  # computed subnet CIDR, e.g. "10.0.0.0/24"
    is_gateway: bool = False
    is_dns: bool = False
    is_switch: bool = False
    is_access_point: bool = False
    role_label: str | None = None


class ScopeTimeseriesPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: str
    count: int


class ScopeSeverityBucket(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    severity: str
    count: int


class ScopePortBucket(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service: str
    count: int


class ScopePort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    port: int
    proto: str
    state: str
    service: str | None = None
    product: str | None = None
    version: str | None = None


class ScopeFinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    kind: str
    severity: str
    title: str
    detail: dict[str, Any] | None = None
    count: int
    first_seen: datetime
    last_seen: datetime


class ScopeHostDetail(BaseModel):
    """Side-panel payload for one host."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    ip: str | None = None
    hostname: str | None = None
    os: str | None = None
    origins: list[str] = []
    managed: bool = False
    monitored: bool = False
    ports: list[ScopePort] = []
    findings: list[ScopeFinding] = []
    recent_actions: list[dict[str, Any]] = []


class ScopeInventoryHost(BaseModel):
    """One row in the discovered-host inventory table (deduped by IP)."""

    model_config = ConfigDict(from_attributes=True)

    discovered_host_id: str  # latest discovered-host row for this IP
    ip: str
    hostname: str | None = None
    mac: str | None = None
    os: str | None = None
    status: str | None = None
    managed: bool = False
    open_port_count: int = 0
    finding_count: int = 0
    services: list[str] = []  # e.g. ["22/tcp ssh", "443/tcp https"]
    scan_target: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class ScopeDeleteRequest(BaseModel):
    """Body for deleting discovered hosts from the Scope inventory by IP."""

    ips: list[str]


class ScopeNetworkRole(BaseModel):
    """A host's manually-tagged network role (gateway/DNS/switch/AP)."""

    model_config = ConfigDict(from_attributes=True)

    ip: str
    is_gateway: bool = False
    is_dns: bool = False
    is_switch: bool = False
    is_access_point: bool = False
    label: str | None = None
    notes: str | None = None


class ScopeNetworkRoleUpdate(BaseModel):
    """Upsert body for a host's network role."""

    ip: str
    is_gateway: bool = False
    is_dns: bool = False
    is_switch: bool = False
    is_access_point: bool = False
    label: str | None = None
    notes: str | None = None


class ScopeSegment(BaseModel):
    """A manual label/color override for a computed subnet grouping."""

    model_config = ConfigDict(from_attributes=True)

    cidr: str
    label: str | None = None
    color: str | None = None


class ScopeSegmentUpdate(BaseModel):
    """Upsert body for a segment's cosmetic override."""

    cidr: str
    label: str | None = None
    color: str | None = None


class ScopeDeleteResult(BaseModel):
    deleted_ips: list[str]
    deleted_hosts: int
    deleted_services: int
    deleted_findings: int


class ScopePromoteRequest(BaseModel):
    """Body for promoting discovered hosts into the managed Server inventory."""

    ips: list[str]
    credential_id: str | None = None


class ScopePromoteResult(BaseModel):
    created: list[str]  # IPs a new Server row was created for
    already_managed: list[str]  # IPs that already had a matching Server
    invalid: list[str]  # IPs that didn't parse and were skipped
