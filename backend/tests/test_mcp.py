import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_mcp_api(db_session: AsyncSession, async_client: AsyncClient):
    # 1. Create Server
    res = await async_client.post(
        "/api/mcp-servers",
        json={"name": "Test Server", "command": "echo", "args": ["hello"], "autostart": False},
    )
    assert res.status_code == 200
    server_id = res.json()["id"]
    assert res.json()["name"] == "Test Server"

    # 2. List Servers
    res = await async_client.get("/api/mcp-servers")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1

    # 3. Get Server
    res = await async_client.get(f"/api/mcp-servers/{server_id}")
    assert res.status_code == 200

    # 4. Patch Server
    res = await async_client.patch(f"/api/mcp-servers/{server_id}", json={"command": "python"})
    assert res.status_code == 200
    assert res.json()["command"] == "python"

    # 5. Delete Server
    res = await async_client.delete(f"/api/mcp-servers/{server_id}")
    assert res.status_code == 200

    res = await async_client.get("/api/mcp-servers")
    assert len(res.json()) == 0
