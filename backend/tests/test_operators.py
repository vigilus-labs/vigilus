import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.db.models import Provider, Tool, Operator, PermissionLevel, TrustMode
from vigilus.db.seed import run_seed

@pytest.mark.asyncio
async def test_operator_crud(db_session: AsyncSession, async_client: AsyncClient):
    # 1. Create a Provider and a Tool first
    provider = Provider(name="Test Provider", type="openai_compat", base_url="http://localhost:8000")
    db_session.add(provider)
    tool = Tool(name="test_tool", required_permission=PermissionLevel.read)
    db_session.add(tool)
    await db_session.commit()
    
    # 2. Create Operator
    payload = {
        "name": "Test Operator",
        "description": "A test operator",
        "system_prompt": "You are a test operator.",
        "provider_id": provider.id,
        "model": "gpt-test",
        "permission_level": "read",
        "trust_mode": "strict",
        "working_dir": "/tmp",
        "tool_ids": [tool.id]
    }
    
    res = await async_client.post("/api/operators", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["name"] == "Test Operator"
    assert tool.id in data["tool_ids"]
    op_id = data["id"]
    
    # 3. Get Operator
    res = await async_client.get(f"/api/operators/{op_id}")
    assert res.status_code == 200
    
    # 4. List Operators
    res = await async_client.get("/api/operators")
    assert res.status_code == 200
    assert len(res.json()) >= 1
    
    # 5. Update Operator
    res = await async_client.patch(f"/api/operators/{op_id}", json={"name": "Updated Name"})
    assert res.status_code == 200
    assert res.json()["name"] == "Updated Name"
    
    # 6. Delete Operator
    res = await async_client.delete(f"/api/operators/{op_id}")
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_seed_preserves_user_edits_to_builtin_operators(db_session: AsyncSession):
    """Re-running the seed (every startup) must NOT clobber user customisations
    of built-in operators — permission level, trust mode, system prompt, enabled.

    Regression: the seed used to overwrite these fields on every boot, reverting
    any change made through the UI back to the built-in default.
    """
    await run_seed(db_session)
    await db_session.commit()

    op = (
        await db_session.execute(select(Operator).where(Operator.is_builtin == True))  # noqa: E712
    ).scalars().first()
    assert op is not None

    # Simulate a user editing the operator and saving.
    new_perm = (
        PermissionLevel.write
        if op.permission_level != PermissionLevel.write
        else PermissionLevel.read
    )
    new_trust = (
        TrustMode.lenient if op.trust_mode != TrustMode.lenient else TrustMode.strict
    )
    op.permission_level = new_perm
    op.trust_mode = new_trust
    op.system_prompt = "CUSTOM PROMPT EDITED BY USER"
    op.enabled = False
    name = op.name
    await db_session.commit()

    # Reboot: the seed runs again.
    await run_seed(db_session)
    await db_session.commit()

    refreshed = (
        await db_session.execute(select(Operator).where(Operator.name == name))
    ).scalar_one()
    assert refreshed.permission_level == new_perm
    assert refreshed.trust_mode == new_trust
    assert refreshed.system_prompt == "CUSTOM PROMPT EDITED BY USER"
    assert refreshed.enabled is False
    # Structural invariant is still re-asserted.
    assert refreshed.is_builtin is True
