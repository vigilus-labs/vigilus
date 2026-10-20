"""Scope ingest only records hosts nmap actually found alive.

A CIDR sweep must not turn every address in the range into a discovered host.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core import scope as core_scope
from vigilus.core.scope import _host_is_live, _parse_nmap_text
from vigilus.db.models import DiscoveredHost

LIVE_HOST = """\
Nmap scan report for plex.local (192.168.1.5)
Host is up (0.00052s latency).
PORT   STATE SERVICE
22/tcp open  ssh
"""

# Up via ping/ARP, nothing open (e.g. ``-sn``, or ``--open`` hiding closed ports).
LIVE_NO_PORTS = """\
Nmap scan report for 192.168.1.7
Host is up (0.0011s latency).
MAC Address: AA:BB:CC:DD:EE:FF (Raspberry Pi Trading)
"""

# ``-v``: nmap reports every address it probed, flagging the dead ones.
DOWN_VERBOSE = "Nmap scan report for 192.168.1.9 [host down]\n"

# ``-v --reason``: the down marker carries the reason, so it isn't "[host down]".
DOWN_WITH_REASON = "Nmap scan report for 192.168.1.10 [host down, received no-response]\n"

# ``-Pn``: nmap assumes every target is up without probing it.
ASSUMED_UP_PLAIN = """\
Nmap scan report for 192.168.1.20
Host is up.
All 1000 scanned ports on 192.168.1.20 are in state: filtered
"""

ASSUMED_UP_WITH_REASON = """\
Nmap scan report for 192.168.1.21
Host is up, received user-set.
All 1000 scanned ports on 192.168.1.21 are in state: filtered
"""

# ``-Pn`` against a host that really is there: the open port proves it.
ASSUMED_UP_BUT_REAL = """\
Nmap scan report for 192.168.1.22
Host is up, received user-set (0.0009s latency).
PORT   STATE SERVICE
80/tcp open  http
"""


def _live_ips(text: str) -> list[str]:
    return [h["ip"] for h in _parse_nmap_text(text) if _host_is_live(h)]


@pytest.mark.parametrize(
    "report, ip",
    [
        (LIVE_HOST, "192.168.1.5"),
        (LIVE_NO_PORTS, "192.168.1.7"),
        (ASSUMED_UP_BUT_REAL, "192.168.1.22"),
    ],
)
def test_live_hosts_are_kept(report: str, ip: str):
    assert _live_ips(report) == [ip]


@pytest.mark.parametrize(
    "report",
    [DOWN_VERBOSE, DOWN_WITH_REASON, ASSUMED_UP_PLAIN, ASSUMED_UP_WITH_REASON],
)
def test_dead_or_unconfirmed_hosts_are_dropped(report: str):
    assert _live_ips(report) == []


def test_mixed_sweep_keeps_only_live_hosts():
    sweep = "\n".join(
        [
            LIVE_HOST,
            DOWN_VERBOSE,
            DOWN_WITH_REASON,
            ASSUMED_UP_PLAIN,
            LIVE_NO_PORTS,
        ]
    )
    assert _live_ips(sweep) == ["192.168.1.5", "192.168.1.7"]


@pytest.mark.asyncio
async def test_ingest_persists_only_live_hosts(db_session: AsyncSession):
    sweep = "\n".join([LIVE_HOST, DOWN_WITH_REASON, ASSUMED_UP_PLAIN, ASSUMED_UP_WITH_REASON])

    summary = await core_scope.ingest(sweep, target="192.168.1.0/24")

    assert summary["hosts"] == 1
    assert summary["scanned"] == 4
    rows = (await db_session.execute(select(DiscoveredHost.ip))).scalars().all()
    assert rows == ["192.168.1.5"]
