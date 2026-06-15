"""Orchestrator research-loop tests: block parsing + run_research (plan §10)."""

from __future__ import annotations

from sqlalchemy import select

from vigilus.core.research import parse_research_blocks, run_research
from vigilus.db.models import Operator
from vigilus.db.seed import run_seed
from vigilus.search.base import SearchResult


class _FakeBackend:
    async def search(self, query, *, max_results=5):
        return [SearchResult(title="NginxDoc", url="https://nginx.org/x", snippet="http3 syntax")]


def test_parse_search_block_fenced():
    text = 'Let me look this up.\n```json\n{"search": "nginx http3 syntax"}\n```'
    cleaned, blocks = parse_research_blocks(text)
    assert blocks == [{"search": "nginx http3 syntax"}]
    assert "search" not in cleaned  # block stripped from visible reply


def test_parse_fetch_block_inline():
    text = 'Reading docs {"fetch": "https://nginx.org/en/docs/x.html"} now.'
    cleaned, blocks = parse_research_blocks(text)
    assert blocks == [{"fetch": "https://nginx.org/en/docs/x.html"}]


def test_parse_no_blocks():
    text = "Just a normal answer with no research."
    cleaned, blocks = parse_research_blocks(text)
    assert blocks == []
    assert cleaned == text


async def test_run_research_executes_and_frames(db_session, monkeypatch):
    await run_seed(db_session)

    import vigilus.tools.native.search as search_mod

    monkeypatch.setattr(search_mod, "build_search_backend", lambda cfg: _FakeBackend())

    framed = await run_research([{"search": "nginx http3 syntax"}], db=db_session)

    assert "RESEARCH RESULTS" in framed
    assert "UNTRUSTED" in framed  # injection-defense framing present
    assert "nginx.org" in framed


async def test_operators_have_no_web_tools_after_seed(db_session):
    """Decision #1: the orchestrator's specialist operators never get web tools."""
    await run_seed(db_session)
    from vigilus.db.models import OperatorTool, Tool

    delegatable = (
        await db_session.execute(select(Operator).where(Operator.delegatable == True))  # noqa: E712
    ).scalars().all()
    for op in delegatable:
        rows = (
            await db_session.execute(
                select(OperatorTool).where(OperatorTool.operator_id == op.id)
            )
        ).scalars().all()
        for r in rows:
            t = await db_session.get(Tool, r.tool_id)
            assert t.name not in ("web_search", "web_fetch")
