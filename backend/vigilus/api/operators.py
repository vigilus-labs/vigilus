from __future__ import annotations

import pydantic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from vigilus.core.operator_runtime import OperatorRuntime
from vigilus.db.base import get_db
from vigilus.db.models import Operator, OperatorTool, Provider, Tool
from vigilus.db.seed import RESEARCH_TOOL_NAMES, VIGILUS_PRINCIPAL_NAME
from vigilus.providers.base import LLMMessage
from vigilus.schemas.operator import OperatorCreate, OperatorResponse, OperatorUpdate

router = APIRouter(prefix="/operators", tags=["Operators"])


async def _reject_research_tools(db: AsyncSession, tool_ids: list[str] | None) -> None:
    """Block assigning the Vigilus-only research tools to a normal operator (plan §3/§5)."""
    if not tool_ids:
        return
    rows = (await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))).scalars().all()
    for t in rows:
        if t.name in RESEARCH_TOOL_NAMES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{t.name}' is reserved for the Vigilus orchestrator and "
                    "cannot be assigned to an operator."
                ),
            )


def _to_response(op: Operator) -> OperatorResponse:
    tool_ids = [ot.tool_id for ot in op.operator_tools] if op.operator_tools else []
    return OperatorResponse(
        id=op.id,
        name=op.name,
        description=op.description,
        system_prompt=op.system_prompt,
        soul=op.soul,
        provider_id=op.provider_id,
        model=op.model,
        permission_level=op.permission_level.value,
        trust_mode=op.trust_mode.value,
        working_dir=op.working_dir,
        is_builtin=op.is_builtin,
        delegatable=op.delegatable,
        enabled=op.enabled,
        icon=op.icon,
        tool_ids=tool_ids,
        created_at=op.created_at,
        updated_at=op.updated_at,
    )


@router.get("", response_model=list[OperatorResponse])
async def list_operators(db: AsyncSession = Depends(get_db)):
    # Hide the reserved Vigilus research principal — it's not a manageable operator.
    result = await db.execute(
        select(Operator)
        .options(selectinload(Operator.operator_tools))
        .where(Operator.name != VIGILUS_PRINCIPAL_NAME)
        .order_by(Operator.name)
    )
    return [_to_response(op) for op in result.scalars().all()]


@router.get("/{operator_id}", response_model=OperatorResponse)
async def get_operator(operator_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Operator)
        .options(selectinload(Operator.operator_tools))
        .where(Operator.id == operator_id)
    )
    op = result.scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")
    return _to_response(op)


@router.post("", response_model=OperatorResponse)
async def create_operator(data: OperatorCreate, db: AsyncSession = Depends(get_db)):
    # Verify provider
    provider = await db.get(Provider, data.provider_id)
    if not provider:
        raise HTTPException(status_code=400, detail="Provider not found")

    await _reject_research_tools(db, data.tool_ids)

    op = Operator(
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        soul=data.soul,
        provider_id=data.provider_id,
        model=data.model,
        permission_level=data.permission_level,
        trust_mode=data.trust_mode,
        working_dir=data.working_dir,
        icon=data.icon,
    )
    db.add(op)
    await db.flush()

    # Assign tools
    if data.tool_ids:
        for tool_id in data.tool_ids:
            tool = await db.get(Tool, tool_id)
            if tool:
                db.add(OperatorTool(operator_id=op.id, tool_id=tool.id))

    await db.commit()
    await db.refresh(op)

    # Need to load tools relation
    result = await db.execute(
        select(Operator).options(selectinload(Operator.operator_tools)).where(Operator.id == op.id)
    )
    return _to_response(result.scalar_one())


@router.patch("/{operator_id}", response_model=OperatorResponse)
async def update_operator(
    operator_id: str, data: OperatorUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Operator)
        .options(selectinload(Operator.operator_tools))
        .where(Operator.id == operator_id)
    )
    op = result.scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")

    if data.name is not None:
        op.name = data.name
    if data.description is not None:
        op.description = data.description
    if data.system_prompt is not None:
        op.system_prompt = data.system_prompt
    if data.soul is not None:
        op.soul = data.soul
    if data.provider_id is not None:
        op.provider_id = data.provider_id
    # model is intentionally nullable: clearing it (override off) makes the
    # operator follow its provider's default model at runtime. Use fields_set so
    # an explicit null clears it, while an omitted field leaves it untouched.
    if "model" in data.model_fields_set:
        op.model = data.model
    if data.permission_level is not None:
        op.permission_level = data.permission_level
    if data.trust_mode is not None:
        op.trust_mode = data.trust_mode
    if data.working_dir is not None:
        op.working_dir = data.working_dir
    if data.enabled is not None:
        op.enabled = data.enabled
    if data.icon is not None:
        op.icon = data.icon

    if data.tool_ids is not None:
        await _reject_research_tools(db, data.tool_ids)
        op.operator_tools.clear()
        for tool_id in data.tool_ids:
            tool = await db.get(Tool, tool_id)
            if tool:
                db.add(OperatorTool(operator_id=op.id, tool_id=tool.id))

    await db.commit()
    await db.refresh(op)

    result = await db.execute(
        select(Operator).options(selectinload(Operator.operator_tools)).where(Operator.id == op.id)
    )
    return _to_response(result.scalar_one())


@router.delete("/{operator_id}")
async def delete_operator(operator_id: str, db: AsyncSession = Depends(get_db)):
    op = await db.get(Operator, operator_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")
    await db.delete(op)
    await db.commit()
    return {"ok": True}


class TestRunRequest(pydantic.BaseModel):
    prompt: str


@router.post("/{operator_id}/test")
async def test_operator(operator_id: str, data: TestRunRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Operator)
        .options(
            selectinload(Operator.operator_tools).selectinload(OperatorTool.tool),
            selectinload(Operator.provider),
        )
        .where(Operator.id == operator_id)
    )
    op = result.scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")

    fallback_provider = None
    if not op.provider:
        from vigilus.db.models import Provider

        fallback_provider = (
            await db.execute(
                select(Provider).where(
                    Provider.is_default == True, Provider.enabled == True  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if not fallback_provider:
            raise HTTPException(
                status_code=400,
                detail="Operator has no provider configured and no default provider is set.",
            )

    runtime = OperatorRuntime(op, fallback_provider=fallback_provider)
    messages = [LLMMessage(role="user", content=data.prompt)]

    try:
        final_msgs, tool_history = await runtime.run(messages)
        return {
            "ok": True,
            "messages": [
                {"role": m.role, "content": str(m.content), "name": m.name} for m in final_msgs
            ],
            "tool_history": tool_history,
        }
    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"ok": False, "error": str(e)}
