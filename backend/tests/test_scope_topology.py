"""Tests for the Scope topology redesign: segment computation, network-role
tagging, segment overrides, and the topology-aware ``GET /scope/hosts``.

These exercise the new tables/APIs from SCOPE_TOPOLOGY_IMPLEMENTATION_PLAN.md
(Phases 1–2, 6). They are unrelated to ``test_jit_scope.py`` (JIT *resource*
scoping)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.api.scope import _compute_segment
from vigilus.db.models import (
    DiscoveredHost,
    NetworkRole,
    NetworkSegment,
    Scan,
    ScopeSource,
)

# ── _compute_segment unit tests ──────────────────────────────


def test_compute_segment_inside_scan_cidr():
    assert _compute_segment("10.0.10.5", "10.0.10.0/24") == "10.0.10.0/24"


def test_compute_segment_outside_scan_cidr_falls_back_to_24():
    # IP not inside the scanned CIDR → its own /24.
    assert _compute_segment("10.0.20.5", "10.0.10.0/24") == "10.0.20.0/24"


def test_compute_segment_no_scan_target():
    assert _compute_segment("192.168.1.50", None) == "192.168.1.0/24"


def test_compute_segment_unparsable_scan_target_falls_back():
    # Garbage scan target is ignored; IP still resolves to its /24.
    assert _compute_segment("10.0.0.7", "not-a-cidr") == "10.0.0.0/24"


def test_compute_segment_unparsable_ip_returns_none():
    assert _compute_segment("not-an-ip", "10.0.10.0/24") is None


def test_compute_segment_ipv6_uses_64():
    # IPv6 hosts group on their own /64, not a /24.
    assert _compute_segment("2001:db8::5", None) == "2001:db8::/64"
    # An IPv6 host outside an IPv4 scan target falls back to /64 too.
    assert _compute_segment("2001:db8::5", "10.0.0.0/24") == "2001:db8::/64"


# ── POST /scope/hosts/role ───────────────────────────────────


@pytest.mark.asyncio
async def test_role_upsert_creates_then_updates(
    db_session: AsyncSession, async_client: AsyncClient
):
    # Create.
    res = await async_client.post(
        "/api/scope/hosts/role",
        json={"ip": "10.0.0.1", "is_gateway": True, "label": "Core Router"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ip"] == "10.0.0.1"
    assert body["is_gateway"] is True
    assert body["label"] == "Core Router"
    assert body["is_dns"] is False

    # Upsert (same IP, different flags) → single row, no duplicate-key error.
    res2 = await async_client.post(
        "/api/scope/hosts/role",
        json={"ip": "10.0.0.1", "is_dns": True, "is_gateway": True},
    )
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    assert body2["is_dns"] is True
    assert body2["is_gateway"] is True
    # Label cleared on upsert (sent as null).
    assert body2["label"] is None

    result = await db_session.execute(select(NetworkRole).where(NetworkRole.ip == "10.0.0.1"))
    rows = result.scalars().all()
    assert len(rows) == 1  # unique constraint on ip holds


@pytest.mark.asyncio
async def test_role_blank_ip_rejected(db_session: AsyncSession, async_client: AsyncClient):
    res = await async_client.post("/api/scope/hosts/role", json={"ip": "   ", "is_gateway": True})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_role_invalid_ip_rejected(db_session: AsyncSession, async_client: AsyncClient):
    res = await async_client.post(
        "/api/scope/hosts/role", json={"ip": "999.999.999.999", "is_gateway": True}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_role_label_too_long_rejected(db_session: AsyncSession, async_client: AsyncClient):
    res = await async_client.post(
        "/api/scope/hosts/role",
        json={"ip": "10.0.0.1", "label": "x" * 256},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_role_notes_round_trip(db_session: AsyncSession, async_client: AsyncClient):
    res = await async_client.post(
        "/api/scope/hosts/role",
        json={"ip": "10.0.0.1", "is_dns": True, "notes": "by the router"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["notes"] == "by the router"


# ── POST /scope/segments ─────────────────────────────────────


@pytest.mark.asyncio
async def test_segment_upsert_creates_then_updates(
    db_session: AsyncSession, async_client: AsyncClient
):
    res = await async_client.post(
        "/api/scope/segments",
        json={"cidr": "10.0.10.0/24", "label": "VLAN 10", "color": "#7c3aed"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cidr"] == "10.0.10.0/24"
    assert body["label"] == "VLAN 10"
    assert body["color"] == "#7c3aed"

    # Upsert same CIDR → updates label/color, still one row.
    res2 = await async_client.post(
        "/api/scope/segments",
        json={"cidr": "10.0.10.0/24", "label": "IoT"},
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["label"] == "IoT"
    assert res2.json()["color"] is None

    result = await db_session.execute(
        select(NetworkSegment).where(NetworkSegment.cidr == "10.0.10.0/24")
    )
    rows = result.scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_segment_malformed_cidr_rejected(db_session: AsyncSession, async_client: AsyncClient):
    res = await async_client.post("/api/scope/segments", json={"cidr": "not-a-cidr", "label": "x"})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_segment_blank_cidr_rejected(db_session: AsyncSession, async_client: AsyncClient):
    res = await async_client.post("/api/scope/segments", json={"cidr": "", "label": "x"})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_segment_bad_color_rejected(db_session: AsyncSession, async_client: AsyncClient):
    # Non-hex colors are interpolated into CSS on the client; reject them.
    res = await async_client.post(
        "/api/scope/segments", json={"cidr": "10.0.0.0/24", "color": "not-a-hex"}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_segment_label_too_long_rejected(db_session: AsyncSession, async_client: AsyncClient):
    res = await async_client.post(
        "/api/scope/segments", json={"cidr": "10.0.0.0/24", "label": "x" * 256}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_segment_valid_hex_color_accepted(
    db_session: AsyncSession, async_client: AsyncClient
):
    res = await async_client.post(
        "/api/scope/segments", json={"cidr": "10.0.0.0/24", "color": "#7c3aed"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["color"] == "#7c3aed"


# ── GET /scope/segments ──────────────────────────────────────


@pytest.mark.asyncio
async def test_segments_only_returns_overrides(db_session: AsyncSession, async_client: AsyncClient):
    # Seed one override.
    db_session.add(NetworkSegment(cidr="10.0.10.0/24", label="IoT", color="#abc"))
    await db_session.commit()

    res = await async_client.get("/api/scope/segments")
    assert res.status_code == 200
    rows = res.json()
    # Only the overridden segment comes back; others aren't fabricated.
    assert [r["cidr"] for r in rows] == ["10.0.10.0/24"]
    assert rows[0]["label"] == "IoT"


# ── GET /scope/hosts (segment + role attachment) ─────────────


async def _seed_scan_with_host(
    db: AsyncSession, target: str, ip: str, hostname: str | None = None
) -> str:
    """Insert a completed scan + one discovered host; return the host id."""
    scan = Scan(source=ScopeSource.nmap, target=target, status="completed", host_count=1)
    db.add(scan)
    await db.flush()
    host = DiscoveredHost(scan_id=scan.id, ip=ip, hostname=hostname, status="up")
    db.add(host)
    await db.commit()
    return host.id


@pytest.mark.asyncio
async def test_hosts_endpoint_attaches_segment_and_role(
    db_session: AsyncSession, async_client: AsyncClient
):
    host_id = await _seed_scan_with_host(db_session, "10.0.10.0/24", "10.0.10.5", "cam-01")
    # Tag that IP as the gateway.
    db_session.add(NetworkRole(ip="10.0.10.5", is_gateway=True, label="Router"))
    await db_session.commit()

    res = await async_client.get("/api/scope/hosts")
    assert res.status_code == 200, res.text
    nodes = res.json()
    by_id = {n["id"]: n for n in nodes}
    assert host_id in by_id
    node = by_id[host_id]
    assert node["segment"] == "10.0.10.0/24"
    assert node["is_gateway"] is True
    assert node["role_label"] == "Router"
    # Untagged roles default to False.
    assert node["is_dns"] is False
    assert node["is_switch"] is False
    assert node["is_access_point"] is False


@pytest.mark.asyncio
async def test_hosts_endpoint_segments_hosts_into_distinct_subnets(
    db_session: AsyncSession, async_client: AsyncClient
):
    """Two hosts in two scanned subnets land in two distinct segment groups."""
    await _seed_scan_with_host(db_session, "10.0.0.0/24", "10.0.0.10", "srv")
    await _seed_scan_with_host(db_session, "10.0.10.0/24", "10.0.10.5", "cam")

    res = await async_client.get("/api/scope/hosts")
    assert res.status_code == 200, res.text
    nodes = res.json()
    segments = {n["segment"] for n in nodes if n["segment"]}
    assert segments == {"10.0.0.0/24", "10.0.10.0/24"}


@pytest.mark.asyncio
async def test_hosts_endpoint_no_role_defaults_false(
    db_session: AsyncSession, async_client: AsyncClient
):
    host_id = await _seed_scan_with_host(db_session, "10.0.0.0/24", "10.0.0.20", "pc")
    res = await async_client.get("/api/scope/hosts")
    node = next(n for n in res.json() if n["id"] == host_id)
    assert node["is_gateway"] is False
    assert node["is_dns"] is False
    assert node["role_label"] is None
