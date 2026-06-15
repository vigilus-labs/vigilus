"""Scope persistence logic — shared by the native ingest tool and the
auto-ingest hook in the tool registry.

The central lesson baked in here: **tool output that should be structured must
not depend on the LLM remembering to copy it somewhere.** Scanners (the nmap
MCP tool) return plain text; this module parses that text and persists it so
the Scope map populates automatically.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any

import structlog
from sqlalchemy import select

from vigilus.db.models import (
    DiscoveredHost,
    DiscoveredService,
    Finding,
    FindingKind,
    FindingSeverity,
    Scan,
    ScopeSource,
    Server,
)

logger = structlog.get_logger(__name__)

# Services/ports considered inherently risky when found open. Recorded as
# exposure findings automatically so the Scope charts carry meaningful data
# without waiting on the LLM to flag them.
_INSECURE_SERVICES = {
    "telnet": FindingSeverity.high,
    "ftp": FindingSeverity.medium,
    "rsh": FindingSeverity.high,
    "rlogin": FindingSeverity.high,
    "rexec": FindingSeverity.high,
    "ms-wbt-server": FindingSeverity.medium,   # RDP exposed
    "netbios-ssn": FindingSeverity.medium,
    "smb": FindingSeverity.medium,
    "redis": FindingSeverity.high,
    "mongodb": FindingSeverity.medium,
    "elasticsearch": FindingSeverity.medium,
    "cassandra": FindingSeverity.medium,
    "memcached": FindingSeverity.medium,
    "vnc": FindingSeverity.medium,
    "x11": FindingSeverity.high,
}


async def ingest(
    raw: str,
    *,
    target: str | None = None,
    operator_id: str | None = None,
) -> dict[str, Any]:
    """Parse scanner output and persist Scan + DiscoveredHost/Service rows.

    Detects the format automatically:
      * nmap XML  (``<nmaprun …>``)
      * standard nmap text output (``Nmap scan report for …``)
      * JSON (tolerant — looks for a hosts list in common shapes)

    Returns a summary dict. Never raises — parse failures are reported in the
    result so a caller can surface them without breaking the scan flow.
    """
    if not raw or not raw.strip():
        return {"error": "ingest: empty scan output"}

    stripped = raw.strip()
    if stripped.startswith("<") and "nmaprun" in stripped[:500]:
        hosts = _parse_nmap_xml(stripped)
        parser = "xml"
    elif stripped[0] in "[{":
        hosts, parser = _parse_scan_json(stripped)
    else:
        hosts = _parse_nmap_text(stripped)
        parser = "text"

    if not hosts:
        return {"hosts": 0, "parser": parser, "note": "no hosts parsed from output"}

    # Drop hosts that aren't actually alive. A CIDR sweep parses one entry per
    # address in range; without this a /24 would log all 256 even when only a
    # handful respond (verbose nmap emits down hosts; ``-Pn`` marks every
    # target "up" without confirming it).
    scanned = len(hosts)
    hosts = [h for h in hosts if _host_is_live(h)]
    skipped = scanned - len(hosts)

    if not hosts:
        return {
            "hosts": 0,
            "parser": parser,
            "scanned": scanned,
            "skipped_not_live": skipped,
            "note": "no live hosts in scan output",
        }

    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        scan = Scan(
            source=ScopeSource.nmap,
            target=target,
            status="completed",
            host_count=len(hosts),
            raw=stripped[:200000],
            operator_id=operator_id,
        )
        db.add(scan)
        await db.flush()

        host_count = svc_count = matched = exposures = 0
        for h in hosts:
            host_row = DiscoveredHost(
                scan_id=scan.id,
                ip=h["ip"],
                mac=h.get("mac"),
                hostname=h.get("hostname"),
                os_guess=h.get("os"),
                status=h.get("status", "up"),
            )
            host_row.matched_server_id = await _match_server(db, h)
            if host_row.matched_server_id:
                matched += 1
                # Auto-fill OS context onto the managed server from the scan,
                # but never clobber values the user (or a prior scan) already set.
                await _autofill_server_os(db, host_row.matched_server_id, h.get("os"))
            db.add(host_row)
            await db.flush()
            host_count += 1
            for p in h.get("ports", []):
                db.add(
                    DiscoveredService(
                        discovered_host_id=host_row.id,
                        port=p["port"],
                        proto=p.get("proto", "tcp"),
                        state=p.get("state", "open"),
                        service=p.get("service"),
                        product=p.get("product"),
                        version=p.get("version"),
                    )
                )
                svc_count += 1
            # Auto-flag insecure services as exposure findings.
            exposures += await _record_exposures(db, host_row, h.get("ports", []))
        await db.commit()

    logger.info(
        "scope.ingest",
        scan_id=scan.id,
        parser=parser,
        hosts=host_count,
        scanned=scanned,
        skipped_not_live=skipped,
        services=svc_count,
        matched=matched,
        exposures=exposures,
        target=target,
    )
    return {
        "scan_id": scan.id,
        "parser": parser,
        "hosts": host_count,
        "scanned": scanned,
        "skipped_not_live": skipped,
        "services": svc_count,
        "matched_to_inventory": matched,
        "exposures_flagged": exposures,
        "target": target,
    }


async def record_findings(findings_in: list[dict]) -> dict[str, int]:
    """Persist findings (idempotent via fingerprint). Used by SOC/Recon tools."""
    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    created = updated = 0
    async with factory() as db:
        for f in findings_in:
            host_key = str(
                f.get("server_id") or f.get("discovered_host_id") or f.get("host_identifier") or ""
            )
            fp_src = f.get("source", "custom")
            fp = hashlib.sha256(
                f"{fp_src}|{f.get('kind')}|{f.get('title')}|{host_key}".encode()
            ).hexdigest()[:32]
            existing = (
                await db.execute(select(Finding).where(Finding.fingerprint == fp))
            ).scalar_one_or_none()
            if existing:
                existing.count += 1
                updated += 1
            else:
                db.add(
                    Finding(
                        source=ScopeSource(fp_src),
                        kind=FindingKind(f.get("kind", "alert")),
                        severity=FindingSeverity(f.get("severity", "info")),
                        title=f.get("title", "(untitled)"),
                        detail=f.get("detail"),
                        server_id=f.get("server_id"),
                        discovered_host_id=f.get("discovered_host_id"),
                        host_identifier=f.get("host_identifier"),
                        fingerprint=fp,
                    )
                )
                created += 1
        await db.commit()
    logger.info("scope.record_findings", created=created, updated=updated)
    return {"created": created, "updated": updated}


async def list_hosts() -> dict[str, Any]:
    """Recall what Scope knows: managed + discovered hosts (deduped by IP)."""
    from vigilus.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        servers = (await db.execute(select(Server))).scalars().all()
        dh = (
            await db.execute(select(DiscoveredHost).order_by(DiscoveredHost.last_seen.desc()))
        ).scalars().all()

    seen_ips: set[str] = set()
    discovered = []
    for h in dh:
        if h.ip in seen_ips:
            continue
        seen_ips.add(h.ip)
        discovered.append(
            {
                "ip": h.ip,
                "hostname": h.hostname,
                "os": h.os_guess,
                "managed": h.matched_server_id is not None,
            }
        )
    return {
        "managed": [{"name": s.name, "hostname": s.hostname, "ip": s.ip} for s in servers],
        "discovered": discovered,
        "totals": {"managed": len(servers), "discovered_unique": len(discovered)},
    }


# ── matching ─────────────────────────────────────────────────


def _split_os_guess(guess: str | None) -> tuple[str | None, str | None]:
    """Best-effort split of an nmap OS guess into (os_type, os_version).

    Conservative on purpose: only splits a clean "<Name> <X.Y[.Z]>" shape
    (e.g. "Ubuntu 22.04" -> ("Ubuntu", "22.04")). Anything noisier — version
    ranges ("Linux 5.0 - 5.6"), no dotted version ("Windows Server 2019") —
    is returned whole as the type with no version, so we never mangle it.
    """
    if not guess:
        return None, None
    guess = guess.strip()
    # Name must be letters/spaces/.-/ only (no digits), then a dotted version
    # at the very end. The digit-free name guard keeps ranges from splitting.
    m = re.match(r"^([A-Za-z][A-Za-z ./-]*?)[\s,]+v?(\d+(?:\.\d+)+)$", guess)
    if m:
        return m.group(1).strip(), m.group(2)
    return guess, None


async def _autofill_server_os(db, server_id: str, os_guess: str | None) -> None:
    """Populate a managed server's os/os_version from a scan guess, if empty.

    Only fills blanks — user-entered or previously-filled values win. Best
    effort: never raises into the ingest path.
    """
    if not os_guess:
        return
    try:
        server = await db.get(Server, server_id)
        if not server:
            return
        os_type, os_version = _split_os_guess(os_guess)
        changed = False
        if not server.os and os_type:
            server.os = os_type
            changed = True
        if not server.os_version and os_version:
            server.os_version = os_version
            changed = True
        if changed:
            logger.info(
                "scope.server_os_autofilled",
                server=server.name,
                os=server.os,
                os_version=server.os_version,
            )
    except Exception:  # noqa: BLE001
        logger.exception("scope.server_os_autofill_failed", server_id=server_id)


async def _match_server(db, host: dict) -> str | None:
    """IP → hostname matching against managed inventory. Returns server id or None.

    MAC is stored on the DiscoveredHost for future cross-scan dedupe (survives
    DHCP churn) but the Server model has no mac column, so we match on IP first,
    then hostname.
    """
    ip = host.get("ip")
    hostname = (host.get("hostname") or "").lower()
    if ip:
        s = (await db.execute(select(Server).where(Server.ip == ip))).scalar_one_or_none()
        if s:
            return s.id
    if hostname:
        s = (
            await db.execute(select(Server).where(Server.hostname.ilike(hostname)))
        ).scalar_one_or_none()
        if s:
            return s.id
    return None


# ── parsers ───────────────────────────────────────────────────


def _parse_nmap_xml(xml: str) -> list[dict]:
    """Parse nmap XML output. Only open ports are kept."""
    hosts: list[dict] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        logger.warning("scope.nmap_xml_parse_failed", error=str(e))
        return hosts

    for h in root.findall("host"):
        # Liveness lives in <status state=".." reason=".."/>, NOT <state>.
        status_el = h.find("status")
        status = status_el.get("state", "up") if status_el is not None else "up"
        reason = status_el.get("reason") if status_el is not None else None
        addr_el = next(
            (a for a in h.findall("address") if a.get("addrtype") in ("ipv4", "ipv6")),
            None,
        )
        mac_el = next((a for a in h.findall("address") if a.get("addrtype") == "mac"), None)
        hostnames = [
            hn.get("name")
            for hn in h.findall("hostnames/hostname")
            if hn.get("name")
        ]
        os_el = h.find("os/osmatch")
        ports = []
        for p in h.findall("ports/port"):
            st = p.find("state")
            if st is None or st.get("state") != "open":
                continue
            svc = p.find("service")
            ports.append(
                {
                    "port": int(p.get("portid", 0)),
                    "proto": p.get("protocol", "tcp"),
                    "state": "open",
                    "service": svc.get("name") if svc is not None else None,
                    "product": svc.get("product") if svc is not None else None,
                    "version": svc.get("version") if svc is not None else None,
                }
            )
        if addr_el is None or not addr_el.get("addr"):
            continue
        hosts.append(
            {
                "ip": addr_el.get("addr"),
                "mac": mac_el.get("addr") if mac_el is not None else None,
                "hostname": hostnames[0] if hostnames else None,
                "os": os_el.get("name") if os_el is not None else None,
                "status": status,
                "reason": reason,
                "ports": ports,
            }
        )
    return hosts


# Standard nmap text output. Examples it handles:
#   Nmap scan report for plex.local (10.0.0.5)
#   Host is up (0.0005s latency).
#   PORT     STATE SERVICE  VERSION
#   22/tcp   open  ssh      OpenSSH 8.2p1 Ubuntu
#   ...
#   MAC Address: AA:BB:CC:DD:EE:FF (Raspberry Pi Trading)
#   OS details: Linux 5.0 - 5.4
_REPORT_RE = re.compile(
    r"^Nmap scan report for\s+(?:(?P<name>\S+)\s+\((?P<ip1>[0-9a-fA-F:.]+)\)|(?P<ip2>[0-9a-fA-F:.]+))"
)
_PORT_RE = re.compile(
    r"^(?P<port>\d+)/(?P<proto>tcp|udp|sctp)\s+(?P<state>\S+)(?:\s+(?P<service>\S+))?(?:\s+(?P<version>.+))?$"
)
_MAC_RE = re.compile(r"^MAC Address:\s+(?P<mac>[0-9A-Fa-f:]{17})(?:\s+\((?P<vendor>[^)]+)\))?")
_OS_DETAILS_RE = re.compile(r"^OS details:\s+(.+)$")
_RUNNING_RE = re.compile(r"^Running:\s+(.+)$")
_UP_RE = re.compile(r"^Host is\s+(up|down)")


def _parse_nmap_text(text: str) -> list[dict]:
    """Parse standard nmap text (default ``-oN``) output into host dicts."""
    hosts: list[dict] = []
    current: dict | None = None
    in_port_table = False

    def _flush() -> None:
        nonlocal current
        if current and current.get("ip"):
            hosts.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            in_port_table = False
            continue

        m = _REPORT_RE.match(line)
        if m:
            _flush()
            ip = m.group("ip1") or m.group("ip2")
            name = m.group("name")
            # If "name" looks like an IP itself (no parens form with hostname),
            # treat as no hostname.
            hostname = name if name and not _looks_like_ip(name) else None
            # Verbose nmap reports down hosts as "... (ip) [host down]".
            status = "down" if "[host down]" in line else "up"
            current = {
                "ip": ip,
                "mac": None,
                "hostname": hostname,
                "os": None,
                "status": status,
                "reason": None,
                "ports": [],
            }
            in_port_table = False
            continue

        if current is None:
            continue

        up = _UP_RE.match(line)
        if up:
            current["status"] = "up" if up.group(1) == "up" else "down"
            continue

        if line.lstrip().startswith("PORT") and "STATE" in line:
            in_port_table = True
            continue

        if in_port_table:
            pm = _PORT_RE.match(line)
            if pm:
                state = pm.group("state")
                if state in ("open", "open|filtered"):
                    current["ports"].append(
                        {
                            "port": int(pm.group("port")),
                            "proto": pm.group("proto"),
                            "state": state,
                            "service": pm.group("service"),
                            "product": _first_token(pm.group("version")),
                            "version": _rest_tokens(pm.group("version")),
                        }
                    )
                continue
            # Non-matching line ends the port table.
            in_port_table = False

        mm = _MAC_RE.match(line)
        if mm:
            current["mac"] = mm.group("mac").lower()
            continue

        om = _OS_DETAILS_RE.match(line)
        if om and not current.get("os"):
            current["os"] = om.group(1).strip()
            continue
        rm = _RUNNING_RE.match(line)
        if rm and not current.get("os"):
            current["os"] = rm.group(1).strip()
            continue

    _flush()
    return hosts


def _parse_scan_json(text: str) -> tuple[list[dict], str]:
    """Tolerant JSON parser for tools that return structured scan results.

    Looks for a list of hosts under common keys; falls back to treating a top
    level list as the host list. Returns (hosts, parser_name).
    """
    hosts: list[dict] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], "text"

    candidates = data
    if isinstance(data, dict):
        for key in ("hosts", "scan", "results", "nmap", "data"):
            if isinstance(data.get(key), list):
                candidates = data[key]
                break

    if not isinstance(candidates, list):
        return [], "json"

    for item in candidates:
        if not isinstance(item, dict):
            continue
        ip = item.get("ip") or item.get("addr") or item.get("address")
        if not ip:
            continue
        ports = []
        for p in item.get("ports", []) or []:
            if not isinstance(p, dict):
                continue
            try:
                ports.append(
                    {
                        "port": int(p.get("port") or p.get("portid") or 0),
                        "proto": p.get("proto") or p.get("protocol") or "tcp",
                        "state": p.get("state") or "open",
                        "service": p.get("service") or p.get("name"),
                        "product": p.get("product"),
                        "version": p.get("version"),
                    }
                )
            except (TypeError, ValueError):
                continue
        hosts.append(
            {
                "ip": str(ip),
                "mac": item.get("mac"),
                "hostname": item.get("hostname") or item.get("name"),
                "os": item.get("os") or item.get("os_guess"),
                "status": item.get("status") or "up",
                "ports": [p for p in ports if p["port"]],
            }
        )
    return hosts, "json"


async def _record_exposures(db, host_row: DiscoveredHost, ports: list[dict]) -> int:
    """Auto-record exposure findings for inherently insecure open services.

    Fingerprint keys on the host IP (not the per-scan discovered_host_id) so a
    persistent exposure (e.g. telnet always open) is ONE finding whose count
    grows across rescans, not N duplicates.
    """
    n = 0
    for p in ports:
        svc = (p.get("service") or "").lower()
        sev = _INSECURE_SERVICES.get(svc)
        if sev is None:
            continue
        fp = hashlib.sha256(
            f"nmap|exposure|Insecure service {svc} open|{host_row.ip}|{p['port']}".encode()
        ).hexdigest()[:32]
        existing = (
            await db.execute(select(Finding).where(Finding.fingerprint == fp))
        ).scalar_one_or_none()
        if existing:
            existing.count += 1
        else:
            db.add(
                Finding(
                    source=ScopeSource.nmap,
                    kind=FindingKind.exposure,
                    severity=sev,
                    title=f"Insecure service '{svc}' open (port {p['port']}/{p.get('proto','tcp')})",
                    detail={"service": svc, "port": p["port"], "product": p.get("product")},
                    host_identifier=host_row.ip,
                    fingerprint=fp,
                )
            )
            n += 1
    return n


# nmap reasons that mean "assumed up, never actually confirmed" — produced by
# -Pn (host discovery skipped). Such a host is only real if a port responded.
_ASSUMED_UP_REASONS = {"user-set", "user-set (-Pn)"}


def _host_is_live(h: dict) -> bool:
    """Whether a parsed host should be persisted to Scope.

    Keeps hosts nmap confirmed up. Drops down hosts (verbose nmap emits a
    record for every scanned address) and ``-Pn`` assumed-up hosts that never
    actually answered — unless an open port proves the host is real.
    """
    if h.get("status") != "up":
        return False
    if (h.get("reason") or "") in _ASSUMED_UP_REASONS and not h.get("ports"):
        return False
    return True


def _looks_like_ip(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F:.]+", s or ""))


def _first_token(s: str | None) -> str | None:
    s = (s or "").strip()
    return s.split()[0] if s else None


def _rest_tokens(s: str | None) -> str | None:
    s = (s or "").strip()
    parts = s.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else None
