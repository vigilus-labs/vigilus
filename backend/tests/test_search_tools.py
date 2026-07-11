"""web_search/web_fetch tool tests: Vigilus-only enforcement + audit (plan §10)."""

from __future__ import annotations

from sqlalchemy import select

from vigilus.db.models import Action, Operator, OperatorTool
from vigilus.db.seed import RESEARCH_TOOL_NAMES, VIGILUS_PRINCIPAL_NAME, run_seed
from vigilus.search.base import SearchResult
from vigilus.tools.registry import ToolRegistry


class _FakeBackend:
    async def search(self, query, *, max_results=5):
        return [SearchResult(title="Doc", url="https://docs.example/x", snippet="hello")]


async def _operator_tool_names(db, op_id: str) -> set[str]:
    rows = (
        (await db.execute(select(OperatorTool).where(OperatorTool.operator_id == op_id)))
        .scalars()
        .all()
    )
    names = set()
    for r in rows:
        from vigilus.db.models import Tool

        t = await db.get(Tool, r.tool_id)
        if t:
            names.add(t.name)
    return names


async def test_seed_blocks_operators_grants_principal(db_session):
    await run_seed(db_session)

    # Decision #1: no delegatable operator has the research tools.
    ops = (
        (await db_session.execute(select(Operator).where(Operator.name != VIGILUS_PRINCIPAL_NAME)))
        .scalars()
        .all()
    )
    for op in ops:
        names = await _operator_tool_names(db_session, op.id)
        assert not (set(RESEARCH_TOOL_NAMES) & names), f"{op.name} must not have web tools"

    # The Vigilus principal owns exactly the research tools and is hidden.
    principal = (
        await db_session.execute(select(Operator).where(Operator.name == VIGILUS_PRINCIPAL_NAME))
    ).scalar_one()
    assert principal.delegatable is False
    pnames = await _operator_tool_names(db_session, principal.id)
    assert set(RESEARCH_TOOL_NAMES) <= pnames


async def test_web_search_rejects_non_vigilus_caller(db_session):
    await run_seed(db_session)
    recon = (
        await db_session.execute(select(Operator).where(Operator.name == "Recon Operator"))
    ).scalar_one()

    result = await ToolRegistry().execute(
        tool_id_or_name="web_search",
        arguments={"query": "anything"},
        operator=recon,
    )
    # Handler hard-rejects; the message steers the operator to ask Vigilus.
    assert "reserved for the Vigilus orchestrator" in (result.output or "")


async def test_web_search_runs_for_vigilus_and_audits(db_session, monkeypatch):
    await run_seed(db_session)
    principal = (
        await db_session.execute(select(Operator).where(Operator.name == VIGILUS_PRINCIPAL_NAME))
    ).scalar_one()

    import vigilus.tools.native.search as search_mod

    monkeypatch.setattr(search_mod, "build_search_backend", lambda cfg: _FakeBackend())

    result = await ToolRegistry().execute(
        tool_id_or_name="web_search",
        arguments={"query": "docs example"},
        operator=principal,
    )
    assert result.success
    assert "docs.example" in result.output

    # Audit: start + end actions attributed to the Vigilus principal.
    actions = (
        (await db_session.execute(select(Action).where(Action.tool_name == "web_search")))
        .scalars()
        .all()
    )
    events = {a.event for a in actions}
    assert "tool_call_start" in events and "tool_call_end" in events
    assert all(a.actor == VIGILUS_PRINCIPAL_NAME for a in actions)
    # The query is logged (not secret); no API key ever lands in args.
    for a in actions:
        assert "firecrawl" not in str(a.args).lower()
