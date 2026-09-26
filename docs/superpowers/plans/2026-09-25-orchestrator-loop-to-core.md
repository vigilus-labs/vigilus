# Orchestrator Loop to Core + Small Cleanups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make web chat, the scheduler and the channel gateway run one turn implementation that lives in `core/`, with no `core → api` imports, and land the small cleanups from #60.

**Architecture:** The orchestrator loop and its helpers move out of `backend/vigilus/api/chat.py` into a new `backend/vigilus/core/orchestrator_loop.py`. The SSE bridge module moves from `api/sse.py` to `core/sse.py` because both the loop and the scheduler depend on it. `core/turn.py` gets `execute_turn()`, which returns the persisted rows; `run_turn()` becomes a thin wrapper that returns the reply text, so the scheduler and gateway don't change. `send_message` keeps its HTTP-only work (409 check, task registration, SSE bridge, JIT forwarding, response mapping) and hands the turn itself to `execute_turn()`, passing the @mention instruction as `system_extra`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, pytest + pytest-asyncio (`asyncio_mode = "auto"`), httpx `ASGITransport`, ruff.

**Spec:** GitHub issues vigilus-labs/vigilus #59 ("refactor(chat): move the orchestrator loop to core and use run_turn everywhere") and #60 ("chore: small cleanups …"). Read both with `gh issue view 59` / `gh issue view 60`.

## Global Constraints

- Scope from #60: the duplicate `DEFAULT_IDENTITY`, the dead `elif` in `core/operator_runtime.py`, and the stale Anthropic default model. **Out of scope:** moving `data/orchestrator.json` into the DB (it needs a migration with a rollback path, so it gets its own PR).
- No DB schema changes and no Alembic migrations.
- No HTTP API changes: same routes, status codes, response schemas, SSE event names and payloads.
- No new dependencies.
- Every command runs from `backend/`. Tests: `.venv/bin/pytest`. Lint: `.venv/bin/ruff check .`. Baseline on `dev` @ `cafa9be`: **453 passed**, ruff clean.
- Do **not** run `ruff format` on the repo. 19 files are already unformatted on `dev`, and reformatting them would bury the diff.
- Work on a branch off `dev`: `git switch -c refactor/turn-core dev`.
- End every commit message with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- The PR description uses `Closes #59` and `Part of #60` (the config-storage item stays open).

## Review Focus

1. **Orchestrator not configured, web chat.** Expect HTTP 500 with the same "No provider configured…" message, no user message saved, and a session that isn't left stuck returning 409. Pinned in Task 1 (`test_unconfigured_orchestrator_returns_500_and_saves_nothing`).
2. **Exception partway through a turn.** Expect HTTP 500, the running task and SSE bridge released, and the next message accepted. Pinned in Task 4 (`test_failed_turn_returns_500_and_frees_the_session`).
3. **@mention in a long conversation that gets compressed.** The "Explicit operator selection" instruction must still be in the system prompt. Today it's dropped when compression rebuilds the prompt; the refactor fixes that. Pinned in Task 4 (`test_mention_instruction_survives_compression`).
4. **First message in a "New Chat".** The Tasks view must show the title derived from the message while the turn runs, not "New Chat". The refactor registers the task before the turn auto-titles the session, so it's easy to regress. Pinned in Task 1 (`test_running_task_is_titled_from_the_first_message`).
5. **SSE `done` event.** It must carry the id of the assistant message the POST returns; the frontend reconciles on it. Pinned in Task 1 (`test_plain_reply_is_persisted_and_returned`).

---

### Task 1: Characterization tests for `POST /api/sessions/{id}/messages`

Nothing tests `send_message` end to end with an LLM today (`tests/test_chat.py` only checks CRUD and the 409). These tests pin current behaviour and must pass **before** any refactor. They patch only things that exist both before and after the refactor: `vigilus.providers.registry.build_provider` (imported inside `resolve_orchestrator_provider` at call time), `orchestrator._config_cache`, and `vigilus.api.chat.StreamBridge` (a module global in `api/chat.py` before and after).

**Files:**
- Create: `backend/tests/test_chat_send_message.py`

**Interfaces:**
- Consumes: none.
- Produces: the fixtures `scripted` and `recorded_bridges` and the class `ScriptedProvider`, which Task 4 extends in the same file.

- [ ] **Step 1: Write the tests**

```python
"""POST /api/sessions/{id}/messages end to end, with a scripted LLM.

Pins the web chat turn's behaviour — persistence, @mention routing, task
title, SSE ``done`` payload, and the unconfigured-provider error — so moving
the turn onto the shared core path can't silently change it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from vigilus.api import chat as chat_api
from vigilus.api.sse import get_bridge  # Task 2 rewrites this to vigilus.core.sse
from vigilus.core import orchestrator as orch
from vigilus.core.tasks import get_task_registry
from vigilus.db.models import Message, MessageRole, Operator, Provider, ProviderType
from vigilus.db.models import Session as ChatSession
from vigilus.providers.base import AgentLLM, LLMResponse


class ScriptedProvider(AgentLLM):
    """Returns canned replies in order and records each call's system prompt."""

    def __init__(self, replies: list[str], on_call=None):
        self.replies = list(replies)
        self.systems: list[str] = []
        self.on_call = on_call
        self.default_model = "scripted-model"

    async def complete(self, messages, *, system=None, tools=None, temperature=0.0, **kwargs):
        self.systems.append(system or "")
        if self.on_call is not None:
            self.on_call()
        return LLMResponse(content=self.replies.pop(0))

    async def test_connection(self) -> bool:
        return True


@pytest.fixture
def scripted(monkeypatch):
    """Make the orchestrator resolve to a ScriptedProvider.

    Uses default orchestrator config (no provider_id → falls back to the
    default enabled Provider row) without touching data/orchestrator.json.
    """
    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())

    def _install(replies: list[str], on_call=None) -> ScriptedProvider:
        provider = ScriptedProvider(replies, on_call=on_call)
        monkeypatch.setattr("vigilus.providers.registry.build_provider", lambda row: provider)
        return provider

    return _install


@pytest.fixture
def recorded_bridges(monkeypatch):
    """Record every event published on bridges that send_message creates."""
    bridges: list = []

    class RecordingBridge(chat_api.StreamBridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events: list[tuple[str, dict]] = []
            bridges.append(self)

        def publish(self, event: str, data: dict | None = None) -> None:
            self.events.append((event, data or {}))
            super().publish(event, data)

    monkeypatch.setattr(chat_api, "StreamBridge", RecordingBridge)
    return bridges


async def _default_provider(db) -> Provider:
    row = Provider(
        name="scripted",
        type=ProviderType.openai_compat,
        base_url="http://scripted.invalid",
        default_model="scripted-model",
        is_default=True,
        enabled=True,
    )
    db.add(row)
    await db.commit()
    return row


async def _new_session(client) -> str:
    res = await client.post("/api/sessions", json={})
    assert res.status_code == 200
    return res.json()["id"]


async def _messages(db, session_id: str) -> list[Message]:
    rows = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    return list(rows.scalars().all())


async def test_plain_reply_is_persisted_and_returned(
    db_session, async_client, scripted, recorded_bridges
):
    await _default_provider(db_session)
    scripted(["Disk usage is fine on every server."])
    sid = await _new_session(async_client)

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "check disk usage"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "assistant"
    assert body["content"] == "Disk usage is fine on every server."

    rows = await _messages(db_session, sid)
    assert [(m.role, m.content) for m in rows] == [
        (MessageRole.user, "check disk usage"),
        (MessageRole.assistant, "Disk usage is fine on every server."),
    ]
    session = await db_session.get(ChatSession, sid)
    assert session.title == "check disk usage"

    # The SSE stream is told which message closed the turn.
    [bridge] = recorded_bridges
    assert ("done", {"session_id": sid, "message_id": body["id"]}) in bridge.events

    # The turn is released.
    assert get_task_registry().get(sid) is None
    assert get_bridge(sid) is None


async def test_mentioned_operator_is_pinned_in_the_system_prompt(
    db_session, async_client, scripted
):
    await _default_provider(db_session)
    db_session.add(
        Operator(name="Infra Ops", description="d", system_prompt="p", enabled=True, delegatable=True)
    )
    await db_session.commit()
    provider = scripted(["On it."])
    sid = await _new_session(async_client)

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "@Infra Ops check disk usage"}
    )

    assert res.status_code == 200
    assert "## Explicit operator selection" in provider.systems[0]
    assert '"Infra Ops"' in provider.systems[0]


async def test_no_mention_means_no_operator_pinning(db_session, async_client, scripted):
    await _default_provider(db_session)
    provider = scripted(["Hello!"])
    sid = await _new_session(async_client)

    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "hi"})

    assert res.status_code == 200
    assert "Explicit operator selection" not in provider.systems[0]


async def test_running_task_is_titled_from_the_first_message(
    db_session, async_client, scripted
):
    await _default_provider(db_session)
    sid = await _new_session(async_client)
    titles: list[str] = []
    scripted(["Done."], on_call=lambda: titles.append(get_task_registry().get(sid).title))

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "restart nginx on web-1\nthanks"}
    )

    assert res.status_code == 200
    assert titles == ["restart nginx on web-1"]


async def test_unconfigured_orchestrator_returns_500_and_saves_nothing(
    db_session, async_client, monkeypatch
):
    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())
    sid = await _new_session(async_client)  # no Provider rows exist

    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "hello"})

    assert res.status_code == 500
    assert "No provider configured" in res.json()["detail"]
    assert await _messages(db_session, sid) == []
    assert get_task_registry().get(sid) is None
```

- [ ] **Step 2: Run the tests against current code**

Run: `.venv/bin/pytest tests/test_chat_send_message.py -v`
Expected: all 5 PASS. These are characterization tests, so a failure means the test is wrong, not the code. Fix the test until it describes current behaviour.

- [ ] **Step 3: Lint**

Run: `.venv/bin/ruff check tests/test_chat_send_message.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_send_message.py
git commit -m "test(chat): pin send_message behaviour before moving the turn to core

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Move `api/sse.py` to `core/sse.py`

`api/sse.py` has no FastAPI dependency: it holds the `StreamBridge`, the bridge registry and the `EVT_*` constants, all runtime plumbing. `core/scheduler.py` already imports it, and so will the loop, so it has to live in `core/`. No compatibility shim: the only importers are inside this repo.

**Files:**
- Move: `backend/vigilus/api/sse.py` → `backend/vigilus/core/sse.py`
- Modify (import paths only): `backend/vigilus/api/chat.py` (top import block and the local import in `stream_session`), `backend/vigilus/core/scheduler.py:189`, `backend/vigilus/integrations/router.py:66`, `backend/tests/test_sse.py:7`, `backend/tests/test_orchestrator_streaming.py:14`, `backend/tests/test_channels_router.py:192,221`, `backend/tests/test_chat_send_message.py`
- Modify (comments): `backend/vigilus/core/delegation.py:104`, `backend/vigilus/core/operator_runtime.py:179`

**Interfaces:**
- Consumes: none.
- Produces: `vigilus.core.sse` exporting `StreamBridge`, `SSEEvent`, `register_bridge`, `unregister_bridge`, `get_bridge`, `EVT_THINKING`, `EVT_DELEGATION_START`, `EVT_TOOL_CALL`, `EVT_TOOL_RESULT`, `EVT_DELEGATION_RESULT`, `EVT_TEXT_DELTA`, `EVT_TEXT_CHUNK`, `EVT_JIT_REQUEST`, `EVT_DONE`, `EVT_ERROR` (unchanged names).

- [ ] **Step 1: Move the file and rewrite imports**

```bash
git mv vigilus/api/sse.py vigilus/core/sse.py
grep -rl "vigilus\.api\.sse" vigilus tests | xargs sed -i 's/vigilus\.api\.sse/vigilus.core.sse/g'
sed -i 's/StreamBridge from api\.sse/StreamBridge from core.sse/' vigilus/core/delegation.py vigilus/core/operator_runtime.py
```

- [ ] **Step 2: Verify nothing still points at the old path**

Run: `grep -rn "api\.sse\|api/sse" vigilus tests`
Expected: no output.

- [ ] **Step 3: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: `458 passed` (453 + 5 from Task 1) and `All checks passed!`. If ruff reports I001 (import order) in a touched file, fix that import block by hand. Don't run `ruff format`.

- [ ] **Step 4: Commit**

```bash
git add -A vigilus tests
git commit -m "refactor(sse): move the stream bridge from api/ to core/

The bridge, its registry and the event names are runtime plumbing used by
the scheduler and (next) the orchestrator loop, not HTTP code.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Move the orchestrator loop to `core/orchestrator_loop.py`

A pure move with renames. Behaviour doesn't change. The layering test goes in first and fails on `core/turn.py`.

**Files:**
- Create: `backend/vigilus/core/orchestrator_loop.py`
- Create: `backend/tests/test_layering.py`
- Modify: `backend/vigilus/api/chat.py` (remove moved code: `_load_db_messages_as_llm` lines 205-236, `_frame_delegation_result` 239-244, `_run_orchestrator` 290-675, `_format_delegation_result` 678-709; update imports and the two call sites in `send_message`)
- Modify: `backend/vigilus/core/turn.py:56-57,85,104` (top-level import instead of the lazy `api.chat` import)
- Modify: `backend/vigilus/core/scheduler.py:153-154` (delete the stale circular-import comment)
- Modify: `backend/tests/test_orchestrator_streaming.py`, `backend/tests/test_orchestrator_empty.py`, `backend/tests/test_budget.py`, `backend/tests/test_operator_limits.py` (import paths, names, monkeypatch targets)

**Interfaces:**
- Consumes: `vigilus.core.sse` (Task 2).
- Produces, in `vigilus.core.orchestrator_loop`:
  - `async def run_orchestrator(llm_history: list[LLMMessage], provider: Any, system_prompt: str, *, db: AsyncSession, session_id: str | None = None, provider_id: str | None = None, provider_type: str | None = None, model: str | None = None, max_delegations: int = 5, bridge: StreamBridge | None = None, cancel_event: Any | None = None, unattended: bool = False) -> list[dict[str, Any]]` (was `api.chat._run_orchestrator`, body unchanged)
  - `def load_db_messages_as_llm(db_messages: list[Message]) -> list[LLMMessage]` (was `_load_db_messages_as_llm`)
  - `def frame_delegation_result(operator_name: str, result_text: str) -> str` (was `_frame_delegation_result`)
  - `def format_delegation_result(result: dict[str, Any]) -> str` (was `_format_delegation_result`)
  - Module globals `execute_delegation` (imported) and `event_bus`. Tests monkeypatch `vigilus.core.orchestrator_loop.execute_delegation`.

- [ ] **Step 1: Write the failing layering test**

`backend/tests/test_layering.py`:

```python
"""Layering guard: vigilus.core must not import from vigilus.api.

api/ is the HTTP layer; core/ is the runtime shared by web chat, the
scheduler and the channel gateway. A core → api import means shared turn
logic has crept back into a route module (issue #59).
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "vigilus" / "core"


def _is_api(module: str | None) -> bool:
    return module is not None and (module == "vigilus.api" or module.startswith("vigilus.api."))


def _api_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _is_api(node.module):
            hits.append(f"{path.name}:{node.lineno} from {node.module}")
        elif isinstance(node, ast.Import):
            hits += [
                f"{path.name}:{node.lineno} import {a.name}" for a in node.names if _is_api(a.name)
            ]
    return hits


def test_core_does_not_import_api():
    offenders = [hit for path in sorted(CORE.rglob("*.py")) for hit in _api_imports(path)]
    assert offenders == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_layering.py -v`
Expected: FAIL, with `offenders` listing `turn.py:57 from vigilus.api.chat`.

- [ ] **Step 3: Create `core/orchestrator_loop.py`**

Start the file with this header and imports:

```python
"""The Vigilus orchestrator loop.

Calls the orchestrator LLM, runs the research / remember / delegation control
blocks it emits, feeds results back, and repeats until it gives a final
answer. Shared by every front door through ``core.turn`` — web chat, the
scheduler, and the channel gateway.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from vigilus.core.delegation import execute_delegation, parse_delegation, strip_delegation
from vigilus.core.events import get_event_bus
from vigilus.core.sse import (
    EVT_DELEGATION_RESULT,
    EVT_DELEGATION_START,
    EVT_ERROR,
    EVT_TEXT_CHUNK,
    EVT_TEXT_DELTA,
    EVT_THINKING,
    StreamBridge,
)
from vigilus.core.stream_text import SafeTextStreamer
from vigilus.core.tasks import TaskCancelled, await_cancelled, get_task_registry
from vigilus.db.base import get_session_factory
from vigilus.db.models import Message, MessageRole
from vigilus.providers.base import LLMMessage, LLMResponse

logger = structlog.get_logger(__name__)
event_bus = get_event_bus()
```

Then **cut** these blocks from `api/chat.py` and paste them below the header **verbatim**. Keep every comment and the local imports inside the function bodies; the only edits are the renames that follow.

| Cut from `api/chat.py` | Paste as |
|---|---|
| `def _load_db_messages_as_llm` (lines 205-236) | `def load_db_messages_as_llm` |
| `def _frame_delegation_result` (lines 239-244) | `def frame_delegation_result` |
| `async def _run_orchestrator` (lines 290-675) | `async def run_orchestrator` |
| `def _format_delegation_result` (lines 678-709) | `def format_delegation_result` |

Update the internal call sites to match: `_frame_delegation_result(` appears twice (in `load_db_messages_as_llm` and near the end of `run_orchestrator`) and becomes `frame_delegation_result(`; `_format_delegation_result(` appears once in `run_orchestrator` and becomes `format_delegation_result(`. Also delete the `# ── Orchestrator Loop ──` section banner from `api/chat.py`.

- [ ] **Step 4: Point `api/chat.py` at the new module**

In `send_message`, change `_load_db_messages_as_llm(` to `load_db_messages_as_llm(` and `await _run_orchestrator(` to `await run_orchestrator(`. Add this import to `api/chat.py`:

```python
from vigilus.core.orchestrator_loop import load_db_messages_as_llm, run_orchestrator
```

Then remove the imports `api/chat.py` no longer uses. Run `.venv/bin/ruff check vigilus/api/chat.py` and delete each name it reports as F401. Expect at least `execute_delegation`, `parse_delegation`, `strip_delegation`, `SafeTextStreamer`, `TaskCancelled`, `await_cancelled`, `get_session_factory`, `LLMResponse`, `EVT_TEXT_CHUNK`. Keep anything ruff doesn't flag.

Replace the module docstring (lines 1-6) with:

```python
"""Chat API – sessions, messages, the WebSocket event feed, and the SSE stream.

The orchestrator loop lives in ``core.orchestrator_loop`` and turns run via
``core.turn`` (shared with the scheduler and the channel gateway); this
module owns only the HTTP side.
"""
```

- [ ] **Step 5: Make `core/turn.py` import from core**

Delete lines 56-57 (the comment and the lazy `from vigilus.api.chat import …`). Add to the top-level imports:

```python
from vigilus.core.orchestrator_loop import load_db_messages_as_llm, run_orchestrator
```

Rename the two uses: `_load_db_messages_as_llm(` → `load_db_messages_as_llm(`, `await _run_orchestrator(` → `await run_orchestrator(`.

In `core/scheduler.py`, delete the two stale comment lines above the lazy imports in `execute_scheduled_task`:

```python
    # Imported here to avoid a circular import at module load
    # (core.turn imports api.chat, which imports core modules).
```

Leave the imports themselves where they are. `tests/test_schedules.py` patches `vigilus.core.turn.run_turn`, which only works because the scheduler imports it at call time.

- [ ] **Step 6: Retarget existing tests**

```bash
sed -i \
  -e 's/from vigilus\.api\.chat import _run_orchestrator/from vigilus.core.orchestrator_loop import run_orchestrator/' \
  -e 's/await _run_orchestrator(/await run_orchestrator(/' \
  -e 's/"vigilus\.api\.chat\.execute_delegation"/"vigilus.core.orchestrator_loop.execute_delegation"/' \
  tests/test_orchestrator_streaming.py tests/test_orchestrator_empty.py tests/test_budget.py
sed -i \
  -e 's/from vigilus\.api\.chat import _format_delegation_result/from vigilus.core.orchestrator_loop import format_delegation_result/' \
  -e 's/_format_delegation_result(/format_delegation_result(/' \
  tests/test_operator_limits.py
```

Run: `grep -rn "_run_orchestrator\|_format_delegation_result\|_load_db_messages_as_llm\|_frame_delegation_result\|api\.chat\.execute_delegation" vigilus tests`
Expected: no output.

- [ ] **Step 7: Verify imports resolve with no cycle, then run everything**

Run: `.venv/bin/python -c "import vigilus.core.turn, vigilus.core.scheduler, vigilus.main"`
Expected: no output, exit 0.

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: `459 passed` (the layering test now passes) and `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git add -A vigilus tests
git commit -m "refactor(chat): move the orchestrator loop from api/chat.py to core

run_orchestrator and its history/format helpers now live in
core/orchestrator_loop.py, so core/turn.py no longer imports api.chat.
A layering test keeps core free of api imports.

Refs #59

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: One turn implementation — `send_message` uses `execute_turn`

`send_message` repeats `run_turn`'s build → save → compress → run → persist sequence with two differences: it adds the @mention instruction, and it returns the persisted assistant row. This task gives `core/turn.py` an `execute_turn()` that returns the rows and makes `send_message` call it. It also fixes two bugs in the chat copy: the @mention instruction is lost when compression rebuilds the prompt, and an exception during compression or prompt building escapes without a clean 500.

**Files:**
- Modify: `backend/vigilus/core/turn.py`
- Modify: `backend/vigilus/api/chat.py` (`send_message`; add `_mention_system_extra`)
- Create: `backend/tests/test_turn.py`
- Modify: `backend/tests/test_chat_send_message.py` (two new tests)

**Interfaces:**
- Consumes: `run_orchestrator`, `load_db_messages_as_llm` (Task 3); `StreamBridge` etc. from `vigilus.core.sse` (Task 2).
- Produces, in `vigilus.core.turn`:
  - `@dataclass class TurnResult: text: str; assistant_message: Message | None; user_message: Message | None`
  - `def turn_title(session: Session, user_text: str) -> str | None`
  - `async def execute_turn(db: AsyncSession, session: Session, user_text: str, *, bridge=None, cancel_event=None, system_extra: str | None = None, save_user_message: bool = True, auto_title: bool = True, unattended: bool = False) -> TurnResult`
  - `async def run_turn(db, session, user_text, **kwargs) -> str` (unchanged behaviour: returns `execute_turn(...).text`)

- [ ] **Step 1: Write failing unit tests for `core/turn.py`**

`backend/tests/test_turn.py`:

```python
"""core.turn: the single orchestrator-turn implementation."""

from __future__ import annotations

import pytest_asyncio

from vigilus.core import orchestrator as orch
from vigilus.core.turn import execute_turn, run_turn, turn_title
from vigilus.db.models import MessageRole, Provider, ProviderType
from vigilus.db.models import Session as ChatSession
from vigilus.providers.base import AgentLLM, LLMResponse


class ScriptedProvider(AgentLLM):
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.default_model = "scripted-model"

    async def complete(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(content=self.replies.pop(0))

    async def test_connection(self) -> bool:
        return True


@pytest_asyncio.fixture
async def chat(db_session, monkeypatch):
    """A default provider, a fresh session, and a hook to script replies."""
    monkeypatch.setattr(orch, "_config_cache", orch.OrchestratorConfig())
    db_session.add(
        Provider(
            name="scripted",
            type=ProviderType.openai_compat,
            base_url="http://scripted.invalid",
            default_model="scripted-model",
            is_default=True,
            enabled=True,
        )
    )
    session = ChatSession(title="New Chat", origin="web")
    db_session.add(session)
    await db_session.commit()

    def _script(replies: list[str]) -> None:
        provider = ScriptedProvider(replies)
        monkeypatch.setattr("vigilus.providers.registry.build_provider", lambda row: provider)

    return session, _script


def _session(title):
    return ChatSession(title=title, origin="web")


def test_turn_title_uses_first_line_of_untitled_session():
    assert turn_title(_session("New Chat"), "restart nginx\nplease") == "restart nginx"
    assert turn_title(_session(None), "restart nginx") == "restart nginx"


def test_turn_title_truncates_long_first_lines():
    title = turn_title(_session("New Chat"), "x" * 80)
    assert title == "x" * 57 + "…"


def test_turn_title_keeps_existing_titles_and_blank_input():
    assert turn_title(_session("Weekly patching"), "anything") == "Weekly patching"
    assert turn_title(_session("New Chat"), "   ") == "New Chat"


async def test_execute_turn_returns_persisted_rows(db_session, chat):
    session, script = chat
    script(["All servers are up."])

    result = await execute_turn(db_session, session, "status?")

    assert result.text == "All servers are up."
    assert result.assistant_message is not None
    assert result.assistant_message.id is not None
    assert result.assistant_message.role == MessageRole.assistant
    assert result.user_message is not None
    assert result.user_message.content == "status?"
    assert session.title == "status?"


async def test_execute_turn_without_saving_user_message(db_session, chat):
    session, script = chat
    script(["Retried."])

    result = await execute_turn(db_session, session, "status?", save_user_message=False)

    assert result.user_message is None
    assert result.text == "Retried."


async def test_run_turn_still_returns_text(db_session, chat):
    session, script = chat
    script(["Plain text."])

    assert await run_turn(db_session, session, "hi") == "Plain text."
```

- [ ] **Step 2: Add failing HTTP tests to `tests/test_chat_send_message.py`**

Append:

```python
async def test_mention_instruction_survives_compression(
    db_session, async_client, scripted, monkeypatch
):
    from vigilus.core.compressor import ContextCompressor

    async def _compressed(self, messages, *, system_tokens=0):
        return messages, "Earlier we patched nginx on web-1."

    monkeypatch.setattr(ContextCompressor, "compress_if_needed", _compressed)
    await _default_provider(db_session)
    db_session.add(
        Operator(name="Infra Ops", description="d", system_prompt="p", enabled=True, delegatable=True)
    )
    await db_session.commit()
    provider = scripted(["On it."])
    sid = await _new_session(async_client)

    res = await async_client.post(
        f"/api/sessions/{sid}/messages", json={"content": "@Infra Ops check disk usage"}
    )

    assert res.status_code == 200
    assert "Earlier we patched nginx on web-1." in provider.systems[0]
    assert "## Explicit operator selection" in provider.systems[0]


async def test_failed_turn_returns_500_and_frees_the_session(
    db_session, async_client, scripted, recorded_bridges, monkeypatch
):
    from vigilus.core.compressor import ContextCompressor

    real_compress = ContextCompressor.compress_if_needed
    calls = 0

    async def _explode_once(self, messages, *, system_tokens=0):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("compressor exploded")
        return await real_compress(self, messages, system_tokens=system_tokens)

    monkeypatch.setattr(ContextCompressor, "compress_if_needed", _explode_once)
    await _default_provider(db_session)
    scripted(["Recovered."])  # the first turn fails before reaching the LLM
    sid = await _new_session(async_client)

    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "hello"})

    assert res.status_code == 500
    assert res.json()["detail"] == "compressor exploded"
    assert get_task_registry().get(sid) is None
    assert get_bridge(sid) is None
    [bridge] = recorded_bridges
    assert ("error", {"error": "compressor exploded"}) in bridge.events
    assert ("done", {"session_id": sid}) in bridge.events

    # The session isn't stuck: the next message is accepted (not a 409).
    res = await async_client.post(f"/api/sessions/{sid}/messages", json={"content": "again"})
    assert res.status_code == 200
    assert res.json()["content"] == "Recovered."
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_turn.py tests/test_chat_send_message.py -v`
Expected:
- `test_turn.py`: collection error, `ImportError: cannot import name 'execute_turn'`.
- `test_mention_instruction_survives_compression`: FAIL. `## Explicit operator selection` is missing because today's `send_message` drops it when compression rebuilds the prompt.
- `test_failed_turn_returns_500_and_frees_the_session`: FAIL or ERROR with `RuntimeError: compressor exploded`. Today, compression runs outside `send_message`'s `try`, so the exception escapes the route.
- The five Task 1 tests: PASS.

- [ ] **Step 4: Implement `execute_turn`, `TurnResult`, `turn_title` in `core/turn.py`**

Replace the module docstring and everything from `async def run_turn` to the end of the file with:

```python
"""Shared orchestrator turn — the one place a turn runs.

Web chat (``api/chat.py``), the scheduler (``core/scheduler.py``) and the
channel gateway (``integrations/router.py``) all run turns through here:
build the prompt → save the user message → compress → orchestrate → persist.
Front doors own only their transport (HTTP response, SSE bridge, task
registration, channel replies).
"""
```

```python
@dataclass
class TurnResult:
    """What a turn produced.

    ``text`` is the last plain-text assistant reply (what the scheduler and
    channels send on). ``assistant_message`` is the last assistant row
    persisted, which can be a delegation plan when the turn stopped early.
    ``user_message`` is the saved user row, or ``None`` when the caller asked
    not to save one.
    """

    text: str
    assistant_message: Message | None
    user_message: Message | None


def turn_title(session: Session, user_text: str) -> str | None:
    """The title *session* has once a turn for *user_text* auto-titles it.

    Untitled / "New Chat" sessions take the first line of the message,
    trimmed to 60 characters; anything else keeps its title.
    """
    if session.title and session.title != "New Chat":
        return session.title
    first = user_text.strip().splitlines()[0] if user_text.strip() else ""
    if not first:
        return session.title
    return (first[:57] + "…") if len(first) > 60 else first


async def execute_turn(
    db: AsyncSession,
    session: Session,
    user_text: str,
    *,
    bridge=None,
    cancel_event=None,
    system_extra: str | None = None,
    save_user_message: bool = True,
    auto_title: bool = True,
    unattended: bool = False,
) -> TurnResult:
    """Persist the user message, run the orchestrator to completion, persist
    the replies, and return what was produced.

    Raises ``OrchestratorNotConfigured`` (before saving anything) if no
    provider is set up.

    Args:
        db: Active async DB session (commits are handled inside).
        session: The ``Session`` row this turn belongs to.
        user_text: The text to feed the orchestrator as a user message.
        bridge: Optional ``StreamBridge`` for live SSE-style events.
        cancel_event: Optional ``asyncio.Event``; the loop stops when set.
        system_extra: Extra text appended to the rendered system prompt,
            including after a compression rebuild.
        save_user_message: Persist ``user_text`` as a ``Message`` row first.
        auto_title: Auto-title an untitled/"New Chat" session (see
            :func:`turn_title`). Callers that set a custom title (e.g. the
            scheduler) should pass ``False``.
        unattended: Scheduled run — operators use the longer JIT wait.
    """
    provider, provider_row, model = await resolve_orchestrator_provider(db)
    cfg = load_orchestrator_config()

    builder = PromptBuilder(db=db, custom_identity=cfg.custom_identity, soul=cfg.soul)
    prompt_obj = await builder.build(session_id=session.id)
    system_prompt = prompt_obj.render()
    if system_extra:
        system_prompt += "\n\n" + system_extra

    user_message: Message | None = None
    if save_user_message:
        user_message = Message(session_id=session.id, role=MessageRole.user, content=user_text)
        db.add(user_message)
        if auto_title:
            session.title = turn_title(session, user_text)
        await db.commit()

    rows = (
        (
            await db.execute(
                select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    llm_history = load_db_messages_as_llm(list(rows))

    compressor = ContextCompressor(
        provider=provider,
        model=model,
        max_tokens=resolve_context_window(provider_row, model),
    )
    llm_history, summary = await compressor.compress_if_needed(
        llm_history, system_tokens=len(system_prompt) // 4
    )
    if summary:
        logger.info("turn.compressed", session_id=session.id)
        prompt_obj = await builder.rebuild_volatile(
            prompt_obj,
            memory_context=("[Previous conversation was compressed. Summary:]\n" + summary),
        )
        system_prompt = prompt_obj.render()
        if system_extra:
            system_prompt += "\n\n" + system_extra

    new_msgs = await run_orchestrator(
        llm_history,
        provider,
        system_prompt,
        db=db,
        session_id=session.id,
        provider_id=provider_row.id,
        provider_type=provider_row.type.value,
        model=model,
        bridge=bridge,
        cancel_event=cancel_event,
        unattended=unattended,
    )

    final_text = ""
    assistant_message: Message | None = None
    for m in new_msgs:
        role = MessageRole(m["role"])
        row = Message(
            session_id=session.id,
            role=role,
            content=m["content"],
            operator_id=m.get("operator_id"),
        )
        db.add(row)
        if role == MessageRole.assistant:
            assistant_message = row
            if isinstance(m["content"], str):
                final_text = m["content"]
    await db.commit()
    if assistant_message is not None:
        await db.refresh(assistant_message)

    return TurnResult(
        text=final_text, assistant_message=assistant_message, user_message=user_message
    )


async def run_turn(db: AsyncSession, session: Session, user_text: str, **kwargs: Any) -> str:
    """Run a turn and return the final assistant text.

    Takes the same keyword arguments as :func:`execute_turn`. Used by the
    scheduler and the channel gateway, which only need the reply text.
    """
    result = await execute_turn(db, session, user_text, **kwargs)
    return result.text
```

Add to the imports at the top of `core/turn.py`: `from dataclasses import dataclass` and `from typing import Any`.

- [ ] **Step 5: Run the unit tests**

Run: `.venv/bin/pytest tests/test_turn.py -v`
Expected: 6 PASS.

- [ ] **Step 6: Rewrite `send_message` on top of `execute_turn`**

In `api/chat.py`, add this helper directly after `_detect_mentioned_operators`:

```python
async def _mention_system_extra(content: str, db: AsyncSession) -> str | None:
    """System-prompt addition pinning delegation to @-mentioned operators."""
    mentioned = await _detect_mentioned_operators(content, db)
    if not mentioned:
        return None
    tagged = ", ".join(f'"{name}"' for name in mentioned)
    return (
        "## Explicit operator selection\n\n"
        f"The user's latest message tags specific operators with @mentions: {tagged}. "
        "Delegate the task to the tagged operator(s) exactly — and in that order if "
        "there is more than one — using the normal delegation format. Do NOT substitute "
        "a different operator or skip the delegation, even if another operator seems "
        "better suited; the user has chosen deliberately. If a tagged operator cannot "
        "do the task, report that back rather than silently picking another."
    )
```

(`execute_turn` joins it with `"\n\n"`, so the rendered prompt is byte-identical to today's `"\n\n## Explicit operator selection\n\n…"`.)

Replace the body of `send_message` from `# ── Resolve orchestrator provider ──` (line 786) through the end of the function with:

```python
    # ── Register this turn so it can be viewed, restored, and cancelled ───
    # Registered before the turn runs, so title it the way the turn will.
    running_task = get_task_registry().register(
        session.id, turn_title(session, data.content) or "Chat"
    )

    # Buffer activity-feed events on the task so a client that navigates away
    # and returns can restore what the turn has been doing.
    activity_events = {
        EVT_THINKING,
        EVT_DELEGATION_START,
        EVT_TOOL_CALL,
        EVT_TOOL_RESULT,
        EVT_DELEGATION_RESULT,
        EVT_TEXT_DELTA,
        EVT_ERROR,
        "loop_detected",
    }

    def _record_activity(event: str, data: dict) -> None:
        if event in activity_events:
            get_task_registry().record(session.id, event, data)

    # ── Create SSE bridge for streaming ───────────────────
    bridge = StreamBridge(on_event=_record_activity)
    register_bridge(session.id, bridge)

    # Forward JIT approval requests raised during this turn into the
    # chat stream so the user can approve inline without leaving the page.
    async def _forward_jit(payload: dict) -> None:
        bridge.publish(EVT_JIT_REQUEST, payload or {})

    event_bus.subscribe("jit.requested", _forward_jit)

    # ── Run the turn (stream events via bridge) ───────────
    try:
        result = await execute_turn(
            db,
            session,
            data.content,
            bridge=bridge,
            cancel_event=running_task.cancel_event,
            # Honor explicit @operator mentions: delegate to exactly those.
            system_extra=await _mention_system_extra(data.content, db),
        )
        bridge.publish(
            EVT_DONE,
            {
                "session_id": session.id,
                "message_id": result.assistant_message.id if result.assistant_message else None,
            },
        )
    except OrchestratorNotConfigured as e:
        bridge.publish(EVT_ERROR, {"error": str(e)})
        bridge.publish(EVT_DONE, {"session_id": session.id})
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("orchestrator.run_failed", error=str(e), session_id=session.id)
        bridge.publish(EVT_ERROR, {"error": str(e)})
        bridge.publish(EVT_DONE, {"session_id": session.id})
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        event_bus.unsubscribe("jit.requested", _forward_jit)
        get_task_registry().unregister(session.id, running_task.id)
        bridge.close()
        unregister_bridge(session.id)

    # Fallback: return the user message if the turn produced no assistant row
    return _message_to_response(result.assistant_message or result.user_message)
```

Update `api/chat.py` imports:
- Add `from vigilus.core.turn import execute_turn, turn_title`.
- Change `from vigilus.core.orchestrator import (OrchestratorNotConfigured, load_orchestrator_config, resolve_orchestrator_provider)` to `from vigilus.core.orchestrator import OrchestratorNotConfigured`.
- Remove the Task 3 import `from vigilus.core.orchestrator_loop import load_db_messages_as_llm, run_orchestrator`.
- Run `.venv/bin/ruff check vigilus/api/chat.py` and delete what it reports as F401. Expect `ContextCompressor`, `resolve_context_window`, `PromptBuilder`, `LLMMessage`.

- [ ] **Step 7: Run the chat and turn tests**

Run: `.venv/bin/pytest tests/test_turn.py tests/test_chat_send_message.py tests/test_chat.py tests/test_sse.py -v`
Expected: all PASS.

- [ ] **Step 8: Full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: `467 passed` (459 + 6 in `test_turn.py` + 2 new HTTP tests) and `All checks passed!`.

Run: `grep -n "PromptBuilder\|ContextCompressor\|Message(session_id" vigilus/api/chat.py`
Expected: no output. `api/chat.py` no longer builds prompts, compresses or persists turn messages.

- [ ] **Step 9: Commit**

```bash
git add vigilus/core/turn.py vigilus/api/chat.py tests/test_turn.py tests/test_chat_send_message.py
git commit -m "refactor(chat): run web chat turns through core.turn.execute_turn

send_message now delegates the build → save → compress → run → persist
sequence to execute_turn and keeps only its HTTP concerns. The @mention
instruction is passed as system_extra, so it is no longer dropped when
compression rebuilds the prompt, and any failure inside the turn now
returns a clean 500 and releases the session.

Closes #59

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: #60 cleanups — duplicate identity, dead branch, stale default model

**Files:**
- Modify: `backend/vigilus/core/orchestrator.py:27-34` (delete the unused `DEFAULT_IDENTITY`)
- Modify: `backend/vigilus/core/operator_runtime.py:305-310` (delete the dead `elif`)
- Modify: `backend/vigilus/providers/catalog.py` (add `ANTHROPIC_DEFAULT_MODEL`)
- Modify: `backend/vigilus/providers/anthropic_provider.py:25`
- Modify: `backend/vigilus/providers/registry.py:32`
- Test: `backend/tests/test_providers.py`

**Interfaces:**
- Consumes: none.
- Produces: `vigilus.providers.catalog.ANTHROPIC_DEFAULT_MODEL: str` (`"claude-opus-4-8"`, the value the catalog already offers).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_providers.py`:

```python
def test_anthropic_fallback_model_is_the_catalog_default():
    """With no model configured, Anthropic uses the catalog's preset — one source of truth."""
    from vigilus.providers.catalog import ANTHROPIC_DEFAULT_MODEL, PROVIDER_CATALOG

    [preset] = [e for e in PROVIDER_CATALOG if e["id"] == "anthropic"]
    assert preset["default_model"] == ANTHROPIC_DEFAULT_MODEL
    assert not ANTHROPIC_DEFAULT_MODEL.startswith("claude-3")

    p = Provider(type=ProviderType.anthropic, name="no-model", api_key=None, default_model=None)
    assert build_provider(p).default_model == ANTHROPIC_DEFAULT_MODEL
    assert AnthropicProvider(api_key="sk-ant-test").default_model == ANTHROPIC_DEFAULT_MODEL
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_providers.py::test_anthropic_fallback_model_is_the_catalog_default -v`
Expected: FAIL with `ImportError: cannot import name 'ANTHROPIC_DEFAULT_MODEL'`.

- [ ] **Step 3: Implement**

In `vigilus/providers/catalog.py`, above `PROVIDER_CATALOG`:

```python
# Fallback model for Anthropic providers with no model configured. Also the
# preset the setup wizard offers, so the two can't drift apart.
ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-8"
```

and in the `"anthropic"` entry change `"default_model": "claude-opus-4-8",` to `"default_model": ANTHROPIC_DEFAULT_MODEL,`.

In `vigilus/providers/anthropic_provider.py`, add `from vigilus.providers.catalog import ANTHROPIC_DEFAULT_MODEL` to the imports and change the constructor signature to:

```python
    def __init__(self, api_key: str, default_model: str = ANTHROPIC_DEFAULT_MODEL):
```

In `vigilus/providers/registry.py`, add `from vigilus.providers.catalog import ANTHROPIC_DEFAULT_MODEL` to the imports and change line 32 to:

```python
            default_model=provider_row.default_model or ANTHROPIC_DEFAULT_MODEL,
```

In `vigilus/core/orchestrator.py`, delete these lines (27-34). Nothing imports this copy; `api/orchestrator.py` and the tests import `DEFAULT_IDENTITY` from `core/prompt_builder.py`:

```python
# Kept for backward compat / migration — the prompt_builder uses its own default.
DEFAULT_IDENTITY = """\
You are Vigilus, the primary security orchestrator for an IT operations platform.

Your ONLY role is to receive user requests and delegate them to specialist \
operators who have the actual tools to complete the work. You cannot run tools \
yourself — you coordinate the operators who can.
"""
```

(Also delete the blank line that follows, so two blank lines remain before `@dataclass`.)

In `vigilus/core/operator_runtime.py`, delete these three lines, which repeat the `if` condition directly above them and can never run:

```python
                # For OpenAI compatibility, also store raw
                elif hasattr(response, "raw") and response.raw:
                    assistant_msg.raw = response.raw
```

- [ ] **Step 4: Verify**

Run: `grep -rn "claude-3-5-sonnet-20241022" vigilus; grep -rn "DEFAULT_IDENTITY" vigilus/core/orchestrator.py | grep -v "prompt_builder's"`
Expected: no output from either command. (The `OrchestratorConfig` comment that mentions "the prompt_builder's DEFAULT_IDENTITY" stays.)

Run: `.venv/bin/python -c "import vigilus.providers.registry, vigilus.api.providers"`
Expected: exit 0. This checks that the catalog import adds no cycle.

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: `468 passed` and `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add vigilus/core/orchestrator.py vigilus/core/operator_runtime.py vigilus/providers tests/test_providers.py
git commit -m "chore: drop duplicate identity and dead branch; update Anthropic fallback model

- Remove the unused DEFAULT_IDENTITY copy in core/orchestrator.py
  (prompt_builder owns it).
- Remove the unreachable elif in operator_runtime that repeated its if.
- Anthropic's fallback model now comes from the provider catalog
  (claude-opus-4-8) instead of the retired claude-3-5-sonnet-20241022.

Part of #60

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Whole-branch verification

**Files:** none changed unless a check fails.

- [ ] **Step 1: Checks from the issues' acceptance criteria**

Run: `grep -rn "vigilus\.api" vigilus/core`
Expected: no output (#59: "No core → api imports").

Run: `grep -rn "run_orchestrator(\|execute_turn(\|run_turn(" vigilus --include='*.py' | grep -v "def "`
Expected: `run_orchestrator(` is called only from `core/turn.py`; `execute_turn(` from `core/turn.py` (`run_turn`) and `api/chat.py`; `run_turn(` from `core/scheduler.py` and `integrations/router.py` (#59: "One turn implementation used by chat, scheduler, and gateway").

- [ ] **Step 2: Full suite and lint, fresh**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: `468 passed`, `All checks passed!`.

- [ ] **Step 3: Smoke-test the real app**

Start the backend with a configured provider (use the `run` skill, or `./start.sh` from the repo root), open `/chat`, and check:
1. Send a plain message. The plan and reply stream live, the reply persists after a reload, and the Tasks tab shows the first line of the message as the title while it runs.
2. Send `@<operator name> <task>`. The orchestrator delegates to that operator.
3. Press Cancel during a delegation. The turn stops with the "⏹ Task cancelled" message and the next message is accepted.
4. Trigger "Run now" on a scheduled task. It completes and its session appears under Tasks.

If no LLM provider is available locally, say so in the PR description instead of claiming these were checked.

- [ ] **Step 4: Review the branch diff**

Run: `git diff --stat dev...HEAD` and read `git diff dev...HEAD -- vigilus/api/chat.py vigilus/core/turn.py`. Confirm that the only behaviour differences are the ones listed below.

**Intended behaviour changes to list in the PR description:**
- The @mention instruction is now kept after context compression (it used to be dropped).
- An exception during prompt building or compression now returns a clean 500 and releases the session. It used to escape the route.
- With no orchestrator provider configured, an open SSE stream now receives an `error` and a `done` event immediately, instead of waiting 5 s for a bridge. The HTTP response (500 + message) is unchanged.
- The Anthropic fallback model (used only when neither the provider row nor the orchestrator/operator sets a model) changes from the retired `claude-3-5-sonnet-20241022` to `claude-opus-4-8`. Calls that used to fail will now succeed, at Opus pricing.

**Known follow-ups (don't do these here):** #60's move of the orchestrator config into the DB; the channel gateway doesn't register its turns in the task registry, so web cancel and the 409 check don't see them; and the scheduler's bridge/task wiring still duplicates `send_message`'s.
