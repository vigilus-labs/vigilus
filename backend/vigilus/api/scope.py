"""Scope API router — aggregate views over the network attack surface.

Joins managed servers, discovered hosts, findings, and actions into the shapes
the Scope frontend consumes. Mostly read-only; the inventory table also allows
deleting discovered hosts by IP.
"""

from __future__ import annotations

import datetime as _dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sa_delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from vigilus.db.base import get_db
from vigilus.db.models import (
    Action,
    DiscoveredHost,
    DiscoveredService,
    Finding,
    Scan,
    Server,
)
from vigilus.schemas.scope import (
    ScopeDeleteRequest,
    ScopeDeleteResult,
    ScopeFinding,
    ScopeHostDetail,
    ScopeHostNode,
    ScopeInventoryHost,
    ScopeOverview,
    ScopePort,
    ScopePortBucket,
    ScopeSeverityBucket,
    ScopeTimeseriesPoint,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/scope", tags=["Scope"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("/overview", response_model=ScopeOverview)
async def overview(db: SessionDep) -> ScopeOverview:
    """Top-line counts for the stat tiles."""
    managed = (await db.execute(select(func.count(Server.id)))).scalar() or 0
    discovered = (
        await db.execute(select(func.count(func.distinct(DiscoveredHost.ip))))
    ).scalar() or 0
    unmanaged = (
        await db.execute(
            select(func.count(func.distinct(DiscoveredHost.ip))).where(
                DiscoveredHost.matched_server_id.is_(None)
            )
        )
    ).scalar() or 0
    open_ports = (await db.execute(select(func.count(DiscoveredService.id)))).scalar() or 0
    findings = (await db.execute(select(func.count(Finding.id)))).scalar() or 0
    return ScopeOverview(
        managed=managed,
        discovered_unique=discovered,
        unmanaged=unmanaged,
        open_ports=open_ports,
        findings=findings,
    )


@router.get("/hosts", response_model=list[ScopeHostNode])
async def hosts(db: SessionDep) -> list[ScopeHostNode]:
    """Every graph node: managed + discovered, merged by identity, with badges.

    A node keyed by IP (falling back to hostname) carries both origins if a
    managed server and a discovered host share that identity.
    """
    servers = (await db.execute(select(Server))).scalars().all()
    # Latest DiscoveredHost per IP (ordered so the first seen wins the dedupe).
    dh_rows = (
        await db.execute(select(DiscoveredHost).order_by(DiscoveredHost.last_seen.desc()))
    ).scalars().all()

    nodes: dict[str, dict[str, Any]] = {}

    # Managed inventory first.
    for s in servers:
        key = s.ip or s.hostname
        nodes[key] = {
            "id": s.id,
            "label": s.name,
            "ip": s.ip,
            "hostname": s.hostname,
            "os": s.os,
            "status": s.status.value if s.status else None,
            "origins": ["managed"],
            "managed": True,
            "discovered_host_id": None,
            "monitored": False,
        }

    # Layer discovered hosts on top.
    for h in dh_rows:
        key = h.ip
        node = nodes.get(key)
        if node is not None:
            if "discovered" not in node["origins"]:
                node["origins"].append("discovered")
            node["discovered_host_id"] = h.id
            if h.matched_server_id:
                node["managed"] = True
            if h.os_guess and not node.get("os"):
                node["os"] = h.os_guess
        else:
            nodes[key] = {
                "id": h.id,
                "label": h.hostname or h.ip,
                "ip": h.ip,
                "hostname": h.hostname,
                "os": h.os_guess,
                "status": h.status,
                "origins": ["discovered"],
                "managed": False,
                "discovered_host_id": h.id,
                "monitored": False,
            }

    # Attach finding + port counts + monitored flag per node.
    for node in nodes.values():
        node["finding_count"] = await _finding_count(db, node)
        node["open_port_count"] = await _port_count(db, node)
        if node["finding_count"] > 0:
            has_wazuh = (
                await db.execute(
                    select(func.count(Finding.id)).where(
                        or_(
                            Finding.server_id == node.get("id"),
                            Finding.discovered_host_id == node.get("discovered_host_id"),
                            Finding.host_identifier == node.get("ip"),
                        ),
                        Finding.source == "wazuh",
                    )
                )
            ).scalar() or 0
            node["monitored"] = has_wazuh > 0

    return [ScopeHostNode(**n) for n in nodes.values()]


@router.get("/inventory", response_model=list[ScopeInventoryHost])
async def inventory(db: SessionDep) -> list[ScopeInventoryHost]:
    """Discovered hosts as a flat, deletable table — deduped by IP.

    For each IP the most recently seen scan row supplies the displayed
    metadata; ``first_seen`` reaches back to the earliest sighting.
    """
    rows = (
        await db.execute(
            select(DiscoveredHost).order_by(DiscoveredHost.last_seen.desc())
        )
    ).scalars().all()

    # Dedupe by IP: first row wins (latest last_seen), but fold in the earliest
    # first_seen across all sightings of the same IP.
    latest: dict[str, DiscoveredHost] = {}
    earliest_seen: dict[str, _dt.datetime] = {}
    for h in rows:
        if h.ip not in latest:
            latest[h.ip] = h
        if h.first_seen is not None:
            prev = earliest_seen.get(h.ip)
            earliest_seen[h.ip] = h.first_seen if prev is None else min(prev, h.first_seen)

    out: list[ScopeInventoryHost] = []
    for ip, h in latest.items():
        svc_rows = (
            await db.execute(
                select(DiscoveredService)
                .where(DiscoveredService.discovered_host_id == h.id)
                .order_by(DiscoveredService.port)
            )
        ).scalars().all()
        services = [
            f"{s.port}/{s.proto}" + (f" {s.service}" if s.service else "") for s in svc_rows
        ]
        finding_count = (
            await db.execute(
                select(func.count(Finding.id)).where(
                    or_(
                        Finding.discovered_host_id == h.id,
                        Finding.host_identifier == ip,
                    )
                )
            )
        ).scalar() or 0
        scan_target = None
        if h.scan_id:
            scan = await db.get(Scan, h.scan_id)
            scan_target = scan.target if scan else None
        out.append(
            ScopeInventoryHost(
                discovered_host_id=h.id,
                ip=ip,
                hostname=h.hostname,
                mac=h.mac,
                os=h.os_guess,
                status=h.status,
                managed=h.matched_server_id is not None,
                open_port_count=len(svc_rows),
                finding_count=finding_count,
                services=services,
                scan_target=scan_target,
                first_seen=earliest_seen.get(ip, h.first_seen),
                last_seen=h.last_seen,
            )
        )
    # Stable, useful default order: most recently seen first.
    out.sort(key=lambda r: r.last_seen or _dt.datetime.min, reverse=True)
    return out


@router.post("/inventory/delete", response_model=ScopeDeleteResult)
async def delete_inventory(req: ScopeDeleteRequest, db: SessionDep) -> ScopeDeleteResult:
    """Delete discovered hosts (across all scans) for the given IPs.

    Removes the discovered-host rows, their services, and any scan-derived
    findings that resolve *only* to that bare IP (so findings tied to a managed
    server are preserved). Managed servers themselves are never touched.
    """
    ips = sorted({ip.strip() for ip in req.ips if ip and ip.strip()})
    if not ips:
        raise HTTPException(status_code=400, detail="No IPs provided")

    host_ids = (
        await db.execute(select(DiscoveredHost.id).where(DiscoveredHost.ip.in_(ips)))
    ).scalars().all()
    if not host_ids:
        return ScopeDeleteResult(deleted_ips=[], deleted_hosts=0, deleted_services=0, deleted_findings=0)

    # Findings: those bound to the discovered hosts, plus bare-IP scan findings
    # (no server/host FK) keyed on the IP. Managed-server findings are spared.
    finding_filter = or_(
        Finding.discovered_host_id.in_(host_ids),
        (Finding.host_identifier.in_(ips))
        & Finding.server_id.is_(None)
        & Finding.discovered_host_id.is_(None),
    )
    deleted_findings = (
        await db.execute(select(func.count(Finding.id)).where(finding_filter))
    ).scalar() or 0
    await db.execute(sa_delete(Finding).where(finding_filter))

    deleted_services = (
        await db.execute(
            select(func.count(DiscoveredService.id)).where(
                DiscoveredService.discovered_host_id.in_(host_ids)
            )
        )
    ).scalar() or 0
    await db.execute(
        sa_delete(DiscoveredService).where(
            DiscoveredService.discovered_host_id.in_(host_ids)
        )
    )

    await db.execute(sa_delete(DiscoveredHost).where(DiscoveredHost.id.in_(host_ids)))
    await db.commit()

    logger.info(
        "scope.inventory_delete",
        ips=ips,
        hosts=len(host_ids),
        services=deleted_services,
        findings=deleted_findings,
    )
    return ScopeDeleteResult(
        deleted_ips=ips,
        deleted_hosts=len(host_ids),
        deleted_services=deleted_services,
        deleted_findings=deleted_findings,
    )


@router.get("/findings/timeseries", response_model=list[ScopeTimeseriesPoint])
async def findings_timeseries(
    db: SessionDep, days: int = Query(30, ge=1, le=365)
) -> list[ScopeTimeseriesPoint]:
    """Findings per day (by first_seen) for the line chart. Cross-dialect safe."""
    since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    rows = (
        await db.execute(
            select(
                func.date(Finding.first_seen).label("day"),
                func.count(Finding.id).label("count"),
            )
            .where(Finding.first_seen >= since)
            .group_by("day")
            .order_by("day")
        )
    ).all()
    return [ScopeTimeseriesPoint(day=str(d), count=c) for d, c in rows if d is not None]


@router.get("/findings/severity", response_model=list[ScopeSeverityBucket])
async def findings_severity(db: SessionDep) -> list[ScopeSeverityBucket]:
    rows = (
        await db.execute(
            select(Finding.severity, func.count(Finding.id)).group_by(Finding.severity)
        )
    ).all()
    return [
        ScopeSeverityBucket(severity=s.value if s else "info", count=c) for s, c in rows
    ]


@router.get("/ports/distribution", response_model=list[ScopePortBucket])
async def ports_distribution(
    db: SessionDep, limit: int = Query(12, ge=1, le=50)
) -> list[ScopePortBucket]:
    rows = (
        await db.execute(
            select(DiscoveredService.service, func.count())
            .group_by(DiscoveredService.service)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [ScopePortBucket(service=svc or "unknown", count=c) for svc, c in rows]


@router.get("/host/{identity}", response_model=ScopeHostDetail)
async def host_detail(identity: str, db: SessionDep) -> ScopeHostDetail:
    """Side-panel payload: ports, findings, recent actions for one host.

    ``identity`` is matched against a managed server id, a discovered host id,
    or an IP (the node.id the frontend carries).
    """
    server = await db.get(Server, identity)
    dhost = None if server is not None else await db.get(DiscoveredHost, identity)
    # Fall back to IP lookup if the identity isn't a known id.
    if server is None and dhost is None:
        server = (
            await db.execute(select(Server).where(Server.ip == identity))
        ).scalar_one_or_none()
        if server is None:
            dhost = (
                await db.execute(select(DiscoveredHost).where(DiscoveredHost.ip == identity))
            ).scalar_one_or_none()
    if server is None and dhost is None:
        raise HTTPException(status_code=404, detail="Host not found in Scope")

    # Cross-resolve: if we have one side, find the matching other side by IP so the
    # detail panel shows ports (from the discovered host) AND actions (from the server).
    if server is not None and dhost is None and server.ip:
        dhost = (
            await db.execute(
                select(DiscoveredHost)
                .where(DiscoveredHost.ip == server.ip)
                .order_by(DiscoveredHost.last_seen.desc())
            )
        ).scalars().first()
    if dhost is not None and server is None and dhost.ip:
        server = (
            await db.execute(select(Server).where(Server.ip == dhost.ip))
        ).scalar_one_or_none()

    origins: list[str] = []
    managed = False
    label = identity
    ip: str | None = None
    hostname: str | None = None
    os_name: str | None = None
    server_id: str | None = None
    dhost_id: str | None = None

    if server is not None:
        origins.append("managed")
        managed = True
        server_id = server.id
        label = server.name
        ip = server.ip
        hostname = server.hostname
        os_name = server.os
    if dhost is not None:
        origins.append("discovered")
        dhost_id = dhost.id
        if not managed:
            label = dhost.hostname or dhost.ip
            ip = dhost.ip
            hostname = dhost.hostname
            os_name = dhost.os_guess
        elif ip is None:
            ip = dhost.ip

    # Ports from the discovered host (most recent scan's services).
    ports: list[ScopePort] = []
    if dhost_id:
        svc_rows = (
            await db.execute(
                select(DiscoveredService).where(
                    DiscoveredService.discovered_host_id == dhost_id
                )
            )
        ).scalars().all()
        ports = [
            ScopePort(
                port=s.port,
                proto=s.proto,
                state=s.state,
                service=s.service,
                product=s.product,
                version=s.version,
            )
            for s in svc_rows
        ]

    # Findings on this host (by any of the identity keys).
    host_keys = []
    if server_id:
        host_keys.append(Finding.server_id == server_id)
    if dhost_id:
        host_keys.append(Finding.discovered_host_id == dhost_id)
    if ip:
        host_keys.append(Finding.host_identifier == ip)
    findings: list[ScopeFinding] = []
    if host_keys:
        f_rows = (
            await db.execute(
                select(Finding).where(or_(*host_keys)).order_by(Finding.last_seen.desc())
            )
        ).scalars().all()
        findings = [
            ScopeFinding(
                id=f.id,
                source=f.source.value,
                kind=f.kind.value,
                severity=f.severity.value,
                title=f.title,
                detail=f.detail,
                count=f.count,
                first_seen=f.first_seen,
                last_seen=f.last_seen,
            )
            for f in f_rows
        ]

    monitored = any(f.source == "wazuh" for f in findings)

    # Recent actions on this server (if managed).
    recent_actions: list[dict[str, Any]] = []
    if server_id:
        a_rows = (
            await db.execute(
                select(Action)
                .where(Action.server_id == server_id)
                .order_by(Action.created_at.desc())
                .limit(10)
            )
        ).scalars().all()
        recent_actions = [
            {
                "id": a.id,
                "event": a.event,
                "tool_name": a.tool_name,
                "outcome": a.outcome.value if a.outcome else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in a_rows
        ]

    return ScopeHostDetail(
        id=server_id or dhost_id or identity,
        label=label,
        ip=ip,
        hostname=hostname,
        os=os_name,
        origins=origins,
        managed=managed,
        monitored=monitored,
        ports=ports,
        findings=findings,
        recent_actions=recent_actions,
    )


# ── helpers ──────────────────────────────────────────────────


async def _finding_count(db: AsyncSession, node: dict[str, Any]) -> int:
    host_keys = []
    if node.get("managed"):
        host_keys.append(Finding.server_id == node.get("id"))
    if node.get("discovered_host_id"):
        host_keys.append(Finding.discovered_host_id == node.get("discovered_host_id"))
    if node.get("ip"):
        host_keys.append(Finding.host_identifier == node.get("ip"))
    if not host_keys:
        return 0
    return (await db.execute(select(func.count(Finding.id)).where(or_(*host_keys)))).scalar() or 0


async def _port_count(db: AsyncSession, node: dict[str, Any]) -> int:
    dhost_id = node.get("discovered_host_id")
    if not dhost_id:
        return 0
    return (
        await db.execute(
            select(func.count(DiscoveredService.id)).where(
                DiscoveredService.discovered_host_id == dhost_id
            )
        )
    ).scalar() or 0
