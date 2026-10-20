# Chat Work Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Vigilus reply in Chat shows what was actually done to produce it: which operators ran, which tools, on which servers, with what arguments, how long each took, and whether it succeeded. The trace stays attached to the message after the turn ends, after a reload, and when reviewing old sessions, including scheduled-task and channel runs.

**Problem today:** The Actions panel and the in-bubble activity list (`frontend/src/pages/Chat/index.tsx`, the `activities` state) live only in memory. They are cleared on every send (`setActivities([])` in `sendMessage`), on every session switch (`selectSession`), and on reload. Once a turn finishes, the transcript shows only the prose answer. To find out whether the agent really ran `systemctl restart nginx` on `pve-02`, a user has to open the global Actions page and match timestamps by hand. That is the main thing a homelab ops tool needs to make obvious, and today it is hidden.

**Architecture:** No new table. One additive migration indexes `actions.session_id`, because the trace reads a session's audit rows on every open. The audit log already has what we need: every `tool_call_end`, `tool_error` and `tool_denied` row in `actions` carries `session_id`, operator, tool, server, args, outcome, error, duration and the full output (`backend/vigilus/tools/registry.py`). Sessions run one turn at a time (`send_message` returns 409 otherwise), so each action can be assigned to a turn by time window. A pure grouping function plus one read endpoint turn the audit rows into per-message traces. This also gives traces to existing history.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, pytest + pytest-asyncio; React + TypeScript (Vite), lucide-react.

## Global Constraints

- Commands run from `backend/` unless stated. Tests: `.venv/bin/pytest`. Lint: `.venv/bin/ruff check .`. Frontend: `cd frontend && npm run build && npm run lint`.
- Do **not** run `ruff format` on the repo.
- Additive only: one new GET route, one index (migration with a tested downgrade) and new optional frontend types. No changes to existing response shapes or SSE event names.
- The new route goes on the authenticated `/api` chat router. **Do not add routes to `chat_router` until the root mount issue is fixed** (see Prerequisite). Otherwise they are also exposed unauthenticated.
- Redact args with `redact_args` (from `vigilus.core.audit`) on read, so rows written before the write-path fix never reach the UI unredacted.
- Tool output is **not** included in the trace payload. It loads on demand through the existing `GET /api/actions/{id}`, so a page of docker logs is not sent with every session load.

## Review Focus

1. **Auth.** `GET /api/sessions/{id}/trace` returns 401 without a session cookie. Pinned by `test_trace_requires_auth`.
2. **Redaction.** A `password`/`token` arg on a historic action row comes back redacted. Pinned by `test_trace_redacts_args`.
3. **Grouping.** Actions from turn N never attach to turn N±1. A parked-then-resumed turn attaches to the final reply. Pinned by the `test_group_*` tests.

---

## Prerequisite — fix unauthenticated chat routes (separate PR, do first)

`backend/vigilus/main.py:305` runs `app.include_router(chat_router)` with no auth dependency, to expose `/ws` at the root. That mounts **every** chat HTTP route at the root as well. Verified on this branch: unauthenticated `GET /sessions` returns `200 []`, while `GET /api/sessions` returns `401`. This means `POST /sessions/{id}/messages` (which runs the orchestrator and its tools) and `DELETE /sessions/{id}` are reachable without login.

- [ ] Move the `/ws` endpoint onto its own `ws_router` in `api/chat.py` and mount only that one at the root.
- [ ] Test in `tests/test_auth_api.py`: with `unauthenticated_client`, `GET /sessions`, `POST /sessions` and `GET /sessions/x/messages` must not return 200. The SPA fallback may serve HTML for GET, so assert there is no JSON API response. `/ws` must still connect with valid auth (existing WS tests).

Related write-path gap: `ToolRegistry.execute` stores raw `args=arguments` in `tool_call_start`, `tool_denied`, `tool_call_end` and `tool_error` rows (`registry.py` ~L230, ~L344, ~L416). It skips `redact_args`, although the README promises "secrets redacted". Wrap those in `redact_args(...)` in the same PR, with a test.

---

## Task 1 — Pure grouping function

**Files:** create `backend/vigilus/core/trace.py`, create `backend/tests/test_trace.py`.

- [ ] Write the failing tests first:
  - `test_group_assigns_actions_to_reply_in_window`: user@t0, assistant@t5, user@t10, assistant@t15. Actions @t2, @t12 attach to their own assistant ids.
  - `test_group_parked_then_resumed_attaches_to_final_reply`: user@t0, assistant("awaiting approval")@t3, assistant(final)@t9, actions @t1, @t6 → both attach to the final reply.
  - `test_group_ignores_tool_call_start`: start/end pairs give a single step.
  - `test_group_turn_without_assistant_reply`: actions in a window with no assistant message (failed or cancelled turn) are dropped, not attached to a neighbouring turn.
  - `test_group_caps_steps`: more than `MAX_STEPS` (200) steps in one turn → first 200 plus `truncated` = remainder.
- [ ] Implement `group_actions_by_reply(messages, actions) -> dict[str, TurnTrace]`:
  - Split the ordered messages into windows that start at each `role == user` message. The last window is open-ended.
  - Within each window, the target is the **last** `role == assistant` message. Skip delegation-result rows, the same way `isDelegationResult` does on the frontend: content is a dict with `operator`.
  - Keep the events `tool_call_end`, `tool_error` and `tool_denied`. Leave everything else out, and list the kept events in one module constant.
- [ ] `.venv/bin/pytest tests/test_trace.py` passes; ruff is clean.

## Task 2 — Trace endpoint

**Files:** `backend/vigilus/schemas/chat.py`, `backend/vigilus/api/chat.py`, `backend/tests/test_trace.py`.

- [ ] Schemas: `TraceStep {action_id, event, actor, operator_id, tool_name, server_id, server_name, args, outcome, error, duration_ms, created_at}` and `TurnTraceResponse {message_id, steps, truncated}`.
- [ ] `GET /sessions/{session_id}/trace` → `list[TurnTraceResponse]`. Returns 404 for an unknown session. Two queries: messages ordered by `created_at`, and actions `where session_id == ... and event in KEPT_EVENTS` with `.options(defer(Action.output))`, the same pattern as `api/actions.py`. Join `Server.name` so the UI can say "on pve-02" instead of showing a UUID. Apply `redact_args` to `args`.
- [ ] Tests: `test_trace_endpoint_shape`, `test_trace_requires_auth` (unauthenticated → 401 on `/api/sessions/{id}/trace`), `test_trace_redacts_args`, `test_trace_unknown_session_404`.
- [ ] Full backend suite and ruff pass.

## Task 3 — Frontend: types, API, loading

**Files:** `frontend/src/types/index.ts`, `frontend/src/lib/api.ts`, `frontend/src/pages/Chat/index.tsx`.

- [ ] Add the `TraceStep` / `TurnTrace` types and `api.getSessionTrace(sessionId)`.
- [ ] Add `const [traces, setTraces] = useState<Record<string, TurnTrace>>({})`, keyed by assistant message id.
- [ ] Fetch the trace next to every `listMessages` call: in `selectSession`, after `sendMessage` finishes, and in the restored-turn completion effect (~L232). A failed trace fetch must never block messages (`.catch(() => {})`).
- [ ] Clear `traces` wherever `messages` is cleared.

## Task 4 — Frontend: `TurnTrace` component

**Files:** create `frontend/src/components/TurnTrace.tsx`; render it in `Chat/index.tsx` under each assistant bubble.

- [ ] Collapsed by default: one quiet line under the bubble, e.g. `⚙ 4 actions · infra-ops, net-ops · 3.2s` plus `· 1 failed` in danger colour, or `· 1 denied` in amber. Hidden when the reply has no steps.
- [ ] Expanded: one row per step with an outcome icon (reuse the `renderActivityIcon` mapping), operator, `tool_name`, "on <server_name>", a one-line args summary (first 2 keys, truncated) and duration. The error text shows inline on failed rows.
- [ ] Clicking a row loads its output lazily through `api.getAction(id)` and shows it in a `max-h-64 overflow-auto` `<pre>`, cached per id. Add an "Open in Actions" link.
- [ ] Match the existing tokens (`text-[12px]`, `text-text-secondary`, `border-border`, `bg-surface`) and the bubble's left alignment. No new dependencies.
- [ ] When a live turn finishes, its persisted trace appears where the live activity list was, so the user sees the activity settle into the transcript instead of disappearing.
- [ ] `npm run build && npm run lint` are clean.

## Task 5 — Verify in the running app

- [ ] Start the backend and `npm run dev`. Send a message that triggers a delegation with at least 2 tool calls. Confirm that:
  - the trace chip appears when the turn ends, and the counts match the Actions page,
  - the trace is still there after a reload and after switching sessions,
  - a sessions-tab scheduled run shows its trace,
  - a denied/JIT step shows as denied, and a failed tool shows its error,
  - expanding a row loads the output, and a secret-looking arg shows `***REDACTED***` (or whatever marker `redact_args` uses).
- [ ] Screenshot the collapsed and expanded states for the PR.

## Out of scope (follow-ups)

- Persisting the orchestrator's `thinking` / routing events (these are not in `actions`).
- Live trace rows streaming into the same component during a turn. The existing activity list covers that for now.
- An explicit `turn_id` on `actions`. Add it later if time-window grouping turns out to be wrong in practice.
