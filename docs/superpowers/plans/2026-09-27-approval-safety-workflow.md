# Approval & Safety Workflow Implementation Plan (#34 → #41 → #54 → #53)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the approval/safety package: revocable JIT grants (#34), durable turns that park on approval instead of holding a coroutine (#41), hardened channel approvals with allowlist enforcement and scope choice (#54), and plan → approve → apply for write/exec/elevate actions (#53).

**Architecture:** Four sequential PRs off `dev`, each on its own branch stacked on the previous:

1. `pr/jit-revoke` — #34. Small: `WardenService.revoke_grant()`, `POST /api/jit/{id}/revoke`, a DB-backed revocation check at token-enforcement points, Revoke button in the JIT page.
2. `pr/durable-turns` — #41. Big: new `turns` table (expand migration), strict-mode JIT waits become **park** (persist checkpoint, release the coroutine) + **resume** (checkpoint re-entry through the operator loop), startup recovery, parked-turn sweeper, `turn.parked` SSE surface.
3. `pr/channel-approvals` — #54. Most of the plumbing already exists (`send_jit_prompt`, button callbacks, `GatewayManager.resolve_jit`, message edit on resolution). Remaining: default-deny allowlist check on button taps (currently **anyone who can tap the button approves** — security gap), identity by platform user id (currently username), Approve once / Approve 15m / Deny scope buttons, JIT delivery to `deliver_to` channels during scheduled runs.
4. `pr/plan-approve-apply` — #53. New `action_plans` table, orchestrator-level plan submission for write/exec/elevate tasks, parking on plan approval (reuses #41), exact-match enforcement in `ToolRegistry.execute`, plan cards in Chat/JIT page and channels.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async + Alembic, pytest + pytest-asyncio (`asyncio_mode = "auto"`), React + TypeScript (Vite), ruff.

**Spec:** GitHub issues `vigilus-labs/vigilus` #34, #41, #54, #53. Read each with `gh issue view <n>` before starting its phase.

## Global Constraints

- Every command runs from `backend/` unless stated. Tests: `.venv/bin/pytest`. Lint: `.venv/bin/ruff check .`. Frontend checks: `cd frontend && npm run build && npm run lint`.
- Baseline on `feat/approval-safety-workflow` (= `dev` @ `ea5346f`): **501 passed**, ruff clean, frontend build clean. Re-verify after each task.
- Do **not** run `ruff format` on the repo.
- Migrations are **additive only** (new tables/columns). Every migration needs a tested downgrade. No destructive changes to existing rows.
- Existing JIT HTTP API and SSE event names are never removed or renamed; only additive routes/fields (`/api/jit/{id}/revoke` is new; `JitStatus.revoked` already exists in the enum).
- Fail closed everywhere: a revoked/denied/expired grant must never authorize a call; an error while checking revocation must deny, not allow.
- Redact secrets in all new log/audit output the same way existing code does (`_redact` / audit-arg redaction).
- End every commit message with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- PR descriptions: `Closes #34` / `Closes #41` / `Closes #54` / `Closes #53` respectively.

## Review Focus

1. **Revoked token reuse.** A revoked grant must fail even when the raw token string is passed via `jit_token` tool args (tokens are stateless HMAC — the DB check is the only thing that catches this). Pinned in Phase 1 (`test_revoked_token_rejected_via_jit_token_args`).
2. **Channel allowlist bypass.** Today `GatewayManager.resolve_jit` approves for *any* tapper. After Phase 3, a non-allowlisted user's tap must be refused and audited. Pinned in Phase 3 (`test_non_allowlisted_user_cannot_approve`).
3. **No coroutine held while parked.** A turn parked on JIT approval must have released its `TaskRegistry` entry and SSE bridge; approving from any surface must resume the same run, including after a backend restart. Pinned in Phase 2 (`test_parked_turn_survives_restart_and_resumes`).
4. **Plan enforcement is exact.** Execution may only run calls listed in the approved plan (tool name + arguments match); any deviation must re-enter the approval flow, not silently execute. Pinned in Phase 4 (`test_deviation_from_approved_plan_is_blocked`).
5. **Fail-closed on revocation-check errors.** If the DB lookup for revocation status fails, the tool call is denied with a clear error, never executed. Pinned in Phase 1 (`test_revocation_check_failure_fails_closed`).

---

## Phase 1 — #34: JIT revocation (`pr/jit-revoke`)

State today: `JITToken.revoked` is always `False` (`core/rbac.py:260`), no revoke endpoint, no UI. `JitStatus.revoked` exists in the enum but nothing sets it. `_find_approved_token` already filters `status == approved`, so reuse lookups stop seeing a revoked grant — but a raw token passed via `jit_token` args stays valid until TTL.

### Task 1.1: `WardenService.revoke_grant`

**Files:**
- Edit: `backend/vigilus/core/rbac.py`
- Test: `backend/tests/test_jit_revoke.py` (new)

**Interfaces:**
- Produces: `WardenService.revoke_grant(db, request_id: str, approver: str = "admin") -> None` — mirrors `deny_request`: loads `JitRequest`, requires `status == JitStatus.approved` (else `ValueError("Invalid request")`), sets `status = revoked`, `resolved_at = now`, `approved_by = approver`, commits, publishes `"jit.resolved"` with the request id/operator/status. The registry's grant cache already clears on `jit.resolved` events — verify, don't re-implement.

- [ ] **Step 1:** Write failing tests: revoke succeeds on an approved request; revoking a pending/denied/already-revoked request raises; `jit.resolved` event carries `status="revoked"`.
- [ ] **Step 2:** Implement `revoke_grant`.
- [ ] **Step 3:** `.venv/bin/pytest backend/tests/test_jit_revoke.py -q` green.

### Task 1.2: DB-backed revocation check at enforcement points

**Files:**
- Edit: `backend/vigilus/core/rbac.py` (helper), `backend/vigilus/tools/registry.py` (call site)
- Test: `backend/tests/test_jit_revoke.py`

**Interfaces:**
- Produces: `WardenService.is_token_active(db, token: str) -> bool` — `validate_token()` signature/HMAC/expiry check **and** a query `JitRequest.token_id == token AND status IN (revoked, expired)` must return no row. On DB error: log `rbac.revocation_check_failed`, return `False` (fail closed).
- Consumes: `ToolRegistry.execute` step 3 replaces `self.warden.validate_token(jit_token)` with `await self.warden.is_token_active(db, jit_token)`; if it returns a token, continue; if `None`, fall through to the denial/JIT path exactly as if no token had been supplied.

- [ ] **Step 1:** Failing tests: (a) approve → grab token → revoke → call tool with `jit_token` args → denied, no execution; (b) same flow with an active token → allowed; (c) DB failure during the revocation lookup (monkeypatch the query to raise) → denied, error surfaced; (d) reuse lookup never returns a revoked grant.
- [ ] **Step 2:** Implement helper + call-site change. Keep the method async; `is_token_active` returns the validated `JITToken` (truthy) or `None` so `token_obj` semantics are unchanged.
- [ ] **Step 3:** Full suite green.

### Task 1.3: Revoke API + frontend button

**Files:**
- Edit: `backend/vigilus/api/jit.py`, `backend/vigilus/schemas/jit.py`, `frontend/src/lib/api.ts`, `frontend/src/pages/Jit/index.tsx`
- Test: `backend/tests/test_jit_api.py` (new or extend)

**Interfaces:**
- Produces: `POST /api/jit/{request_id}/revoke`, body `JitRevokeRequest { approved_by: str = "admin_ui" }` → 200 `JitRequestResponse` with `status="revoked"`; 400 when the request isn't in `approved` state. Route lives beside approve/deny and reuses `_to_response`.
- Frontend: `revokeJit(id)` in `api.ts`; Revoke button on rows with `status === "approved"` in the JIT page (confirm dialog, calls API, invalidates the JIT query), disabled while pending.

- [ ] **Step 1:** API tests (revoke 200/400/404; audit event emitted).
- [ ] **Step 2:** Implement endpoint.
- [ ] **Step 3:** Frontend: api function + button; `npm run build` and `npm run lint` clean.
- [ ] **Step 4:** Full backend suite + ruff green.

---

## Phase 2 — #41: durable turns, park/resume (`pr/durable-turns`)

State today: strict-mode JIT blocks a coroutine in `ToolRegistry._wait_for_jit_resolution` polling the DB every second for up to `jit_wait_seconds_unattended` (1800 s). Turn state (`RunningTask`, SSE bridge, orchestrator/operator message lists) is process-local; a restart marks pending actions "Interrupted" (`main.py:113-124`) and loses in-flight turns.

Design decisions (pin these):

- **Checkpoint, not incremental message persistence.** The orchestrator/operator loops keep their in-memory message lists as today; a new `turns` row snapshots what a resume needs: delegated operator id, operator messages so far (JSON), pending tool call (name/args/resource/permission/operator), jit request id, origin, `deliver_to`, `unattended`, `expires_at`. Message rows are still written only at turn end (no change to `execute_turn` persistence semantics).
- **Park replaces poll** behind `settings.jit_park_resume` (default `True`; kill-switch restores the old wait loop for one release).
- **Resume re-enters the operator loop** at the pending call: execute the approved tool call (the grant now covers it), feed the `ToolResult` into the operator's message list, continue the operator loop, then the orchestrator loop finishes normally through the existing `run_orchestrator` return path.
- **Any `jit.resolved` surface triggers resume** — web API, channel buttons, revoke. The resume handler runs as a background task; scheduled `deliver_to` delivery happens at completion as today.
- **Timeouts deny.** A parked turn whose `expires_at` (now + the relevant `jit_wait_seconds*`) passes is resumed with a fail-closed denied `ToolResult` so the operator reports the failure cleanly.

### Task 2.1: `turns` table + migration

**Files:**
- Edit: `backend/vigilus/db/models.py`
- Create: `backend/vigilus/db/migrations/versions/2026_09_27_XXXX-<rev>_add_turns.py`
- Test: `backend/tests/test_turns_model.py` (new)

**Interfaces:**
- Produces: `TurnStatus(str, enum.Enum)`: `running | awaiting_approval | completed | failed | cancelled`. Model `Turn`: `id`, `session_id` (FK sessions, indexed), `status` (default `running`), `origin` (`web|telegram|discord|schedule`), `operator_id` (delegated operator, nullable), `pending_call` (JSON, nullable), `operator_messages` (JSON, nullable), `jit_request_id` (String, nullable, indexed), `deliver_to` (JSON, nullable), `unattended` (Boolean, default False), `error` (Text, nullable), `expires_at` (DateTime tz, nullable), `created_at`/`updated_at`.
- Migration: create table; downgrade drops it. No writes to existing tables.

- [ ] **Step 1:** Model + migration; `alembic upgrade head` / `alembic downgrade -1` round-trip on a scratch DB.
- [ ] **Step 2:** Model smoke test (insert/load/enum values).

### Task 2.2: Park path in the tool registry

**Files:**
- Edit: `backend/vigilus/tools/registry.py`
- Create: `backend/vigilus/core/turn_park.py`
- Test: `backend/tests/test_turn_park.py` (new)

**Interfaces:**
- Produces: `core/turn_park.py` with `TurnParkContext` (per-turn: session_id, origin, deliver_to, unattended, operator_messages accessor) and `async park_for_approval(db, ctx, req, tool, resource, permission, timeout) -> NoReturn` — writes/updates the `Turn` row (`awaiting_approval`, pending call, operator messages snapshot, `expires_at`), publishes `turn.parked` (new `EventType.TURN_PARKED`) and raises `TurnParked` (exception carrying `turn_id`).
- Edit: `ToolRegistry.execute` — when `is_allowed` fails into the strict-JIT branch and `settings.jit_park_resume` is on **and** a `TurnParkContext` is attached to the call (threaded through `operator_runtime` → `orchestrator_loop` kwargs), call `park_for_approval` instead of `_wait_for_jit_resolution`. Without a context (tests, TUI, legacy callers) keep the existing wait loop.

- [ ] **Step 1:** Failing tests: park writes the row + raises `TurnParked`; kill-switch off → old polling path used; no context → polling path.
- [ ] **Step 2:** Implement. The registry must commit the checkpoint **before** raising.
- [ ] **Step 3:** Suite green.

### Task 2.3: Checkpoint capture through operator runtime → orchestrator loop

**Files:**
- Edit: `backend/vigilus/core/operator_runtime.py`, `backend/vigilus/core/orchestrator_loop.py`, `backend/vigilus/core/turn.py`, `backend/vigilus/core/tasks.py`
- Test: `backend/tests/test_turn_park.py` (extend)

**Interfaces:**
- `run_orchestrator(..., park: TurnParkContext | None = None)` threads down to operator runtime; operator runtime updates `park.operator_messages` after each iteration and passes `park` into `ToolRegistry.execute`.
- `execute_turn` creates the context when `settings.jit_park_resume`; it also registers the `Turn` row (`status=running`) and marks it terminal at the end.
- On `TurnParked` propagating to `execute_turn`: unregister the `RunningTask`, close the SSE bridge after publishing `turn.parked` (so web UIs can show the awaiting state), return a `TurnResult` whose text says the turn is awaiting approval. Web POST /messages returns 200 with that text; no exception leaks to HTTP.

- [ ] **Step 1:** Failing test: strict JIT during a scripted delegation parks the turn — `Turn` row `awaiting_approval` with pending call JSON, task registry entry gone, bridge closed, no held coroutines (assert the waiter task finished).
- [ ] **Step 2:** Thread the context; handle `TurnParked` in `execute_turn`.
- [ ] **Step 3:** Scheduler + gateway paths (`unattended=True`, channel origins) pass origin/deliver_to so resumes deliver correctly. Suite green.

### Task 2.4: Resume service

**Files:**
- Create: `backend/vigilus/core/turn_resume.py`
- Edit: `backend/vigilus/core/rbac.py` (publishes already; no change), `backend/vigilus/main.py` (subscribe at startup)
- Test: `backend/tests/test_turn_resume.py` (new)

**Interfaces:**
- Produces: `async resume_turn(turn_id: str, resolved_status: str)` — load checkpoint; if status ≠ `awaiting_approval`, no-op. On `approved`: re-enter the operator loop at the pending call (grant now covers it — reuse `_find_approved_token`), continue to completion, mark `Turn` terminal, deliver result to `deliver_to` if set. On `denied|revoked|expired`: resume with a fail-closed denied `ToolResult` so the operator reports it. On any resume error: mark `Turn` failed with the error, never re-raise into the event bus.
- Subscribes to `jit.resolved` (payload has request id → look up `Turn` by `jit_request_id`).

- [ ] **Step 1:** Failing tests: (a) approve → run completes, result delivered to a fake `deliver_to`, turn row `completed`; (b) deny → turn completes with denial reported; (c) double-resume / resume-after-terminal → no-op; (d) resume error → turn `failed`.
- [ ] **Step 2:** Implement + subscribe in lifespan.
- [ ] **Step 3:** Restart-safety test (`test_parked_turn_survives_restart_and_resumes`): park → simulate restart (fresh registry/bridges, run the startup hook) → approve → resumed.

### Task 2.5: Startup recovery + sweeper

**Files:**
- Edit: `backend/vigilus/main.py`
- Test: `backend/tests/test_turn_recovery.py` (new)

- [ ] **Step 1:** Startup: `Turn.status == running` → `failed` ("Interrupted — backend restarted"); `awaiting_approval` kept. Extend the sweeper: periodic (60 s) check for `awaiting_approval` past `expires_at` → `resume_turn(turn_id, "expired")`.
- [ ] **Step 2:** Tests for both paths. Suite green.

### Task 2.6: Frontend awaiting state

**Files:**
- Edit: `frontend/src/pages/Chat/index.tsx` (or the task/activity view that renders JIT cards), `frontend/src/types/index.ts` as needed

- [ ] **Step 1:** Handle the `turn.parked` activity event + `awaiting_approval` task state: show an "awaiting approval" chip; when the resumed task re-registers, the existing task-restore/follow flow picks it up (verify manually with two browser tabs: park in one, approve in JIT page in the other, watch the run continue).
- [ ] **Step 2:** `npm run build && npm run lint` clean; backend suite + ruff green.

---

## Phase 3 — #54: channel approvals hardened (`pr/channel-approvals`)

State today: `send_jit_prompt` + button callbacks + `GatewayManager.resolve_jit` + message edit-on-resolution already exist for Telegram and Discord. **Gaps:** (1) `resolve_jit` never checks the `ChannelAccount` allowlist — any tapper approves (security gap); (2) approver identity is `telegram:<username-or-id>` / `discord:<name>` — not the stable `external_user_id`; (3) buttons are Approve/Deny only, no "once / 15m" scope; (4) scheduled runs forward JIT only to the web SSE bridge (`core/scheduler.py:260`), not to `deliver_to` channels.

### Task 3.1: Allowlist enforcement + stable identity

**Files:**
- Edit: `backend/vigilus/integrations/gateway.py`, `backend/vigilus/integrations/telegram.py`, `backend/vigilus/integrations/discord.py`, `backend/vigilus/integrations/base.py`
- Test: `backend/tests/test_gateway_jit.py` (new)

**Interfaces:**
- `ChannelAdapter.set_jit_resolver` / resolver signature grows: `resolve_jit(request_id, approved, approver, *, platform: str, user_id: str, scope: str = "default")` where `scope ∈ {once, ttl, default}`.
- Telegram `_handle_callback` passes `platform="telegram"`, `user_id=str(frm["id"])` (id, not username — usernames change); approver display becomes `telegram:<id>`. Discord `_resolve_jit_button` passes `platform="discord"`, `user_id=str(interaction.user.id)`.
- `GatewayManager.resolve_jit` **default-deny**: look up `ChannelAccount(platform, external_user_id=user_id)`; if missing or not `allowed` → log `gateway.jit_denied_not_allowlisted`, raise `PermissionError("not allowed")` (adapters already render exceptions as the ⚠️ verdict), and do **not** touch the request.
- `scope` maps to the existing `approve_request` kwargs: `once → single_use=True`, `ttl → ttl_minutes=15` (server still clamps to `jit_max_ttl_minutes`).

- [ ] **Step 1:** Failing tests: allowlisted user approves/denies (request resolved, `approved_by=telegram:<id>`); non-allowlisted user's tap raises and leaves the request `pending`; scope `once` produces a single-use grant; scope `ttl` produces a 15-min timed grant.
- [ ] **Step 2:** Implement across the three adapters + gateway.
- [ ] **Step 3:** Suite green.

### Task 3.2: Scope buttons (Approve once / Approve 15m / Deny)

**Files:**
- Edit: `backend/vigilus/integrations/telegram.py`, `backend/vigilus/integrations/discord.py`
- Test: `backend/tests/test_gateway_jit.py` (extend)

- [ ] **Step 1:** Callback data becomes `jit:<approve_once|approve_ttl|deny>:<id>` (Telegram inline keyboard, 3 buttons; Discord: three `ui.Button`s). Adapters map `approve_once`→`scope="once"`, `approve_ttl`→`scope="ttl"`, `deny`→`approved=False`.
- [ ] **Step 2:** Tests for each mapping; stale-button second tap still renders the already-resolved verdict (existing behavior).

### Task 3.3: JIT delivery to `deliver_to` channels on scheduled runs

**Files:**
- Edit: `backend/vigilus/core/scheduler.py` (`_forward_jit`)
- Test: `backend/tests/test_scheduler_jit_delivery.py` (new)

**Interfaces:**
- In `execute_scheduled_task`, when the task's `deliver_to` resolves to a running channel adapter (`get_gateway().send` currently only does plain text — add `get_gateway().send_jit_prompt(platform, chat_id, text, request_id)` that finds the adapter), also send the JIT card there, with the allowlist still enforced on tap. Keep the existing SSE forwarding unchanged. Unsubscribe on run end as today.

- [ ] **Step 1:** Failing test: scheduled task with `deliver_to` → strict JIT → `send_jit_prompt` called on the fake adapter with the request id; allowlisted tap resolves; the parked/scheduler run picks it up (post-#41) or completes after approval (kill-switch path).
- [ ] **Step 2:** Implement. Suite green; ruff clean.

---

## Phase 4 — #53: plan → approve → apply (`pr/plan-approve-apply`)

Depends on Phase 2 (park/resume) and Phase 3 (channel buttons generalized).

Design decisions:

- Plans are **orchestrator-level**: before delegating a task that will need write/exec/elevate, the orchestrator emits the exact call list and the turn parks for plan approval. Reuses `Turn` park/resume with `pending_call` replaced by a `plan_id` reference.
- Enforcement is **exact-match** at `ToolRegistry.execute`: an approved plan authorizes only (tool name + full arguments) pairs it lists (`jit_token` arg excluded from the comparison, resource extracted as today). Any other call during a plan-bound delegation takes the normal JIT path (re-prompt) — deviation is blocked by construction, surfaced as a new JIT request.
- New setting `plan_approval_mode`: `"off" | "write_elevate"` (default `write_elevate`). Only applies to strict-trust operators; lenient operators never plan-gate.

### Task 4.1: `action_plans` table + plan submission

**Files:**
- Edit: `backend/vigilus/db/models.py` (+ additive `plan_id` column on `Action`, nullable), migration
- Edit: `backend/vigilus/core/orchestrator_tools.py` (new orchestrator tool), `backend/vigilus/core/prompt_builder.py` (instruction block)
- Test: `backend/tests/test_plans.py` (new)

**Interfaces:**
- `ActionPlan`: `id`, `session_id` (FK), `operator_id` (delegated), `calls` (JSON: `[{tool, arguments, resource, permission}]`), `status` (`pending|approved|denied|executing|completed|deviated`), `approved_by`, `created_at`, `resolved_at`.
- Orchestrator tool `submit_plan(calls: list[dict])`: validates shape (tool names exist, permission levels ≥ write), creates the row, parks the turn (`Turn.pending_call = {"kind": "plan", "plan_id": ...}`), publishes `plan.requested` (new event). Resume on approval binds the plan to the delegation and continues.
- System prompt: when `plan_approval_mode == "write_elevate"` and the operator's trust mode is strict, add an instruction to submit a plan before any write/exec/elevate delegation.

- [ ] **Step 1:** Migration (additive; downgrade drops). Model tests.
- [ ] **Step 2:** `submit_plan` + prompt block + scripted-LLM test: orchestrator calling `submit_plan` parks the turn; approval resumes into the delegation.

### Task 4.2: Enforcement in `ToolRegistry.execute`

**Files:**
- Edit: `backend/vigilus/tools/registry.py`
- Test: `backend/tests/test_plans.py` (extend)

**Interfaces:**
- `execute(..., plan: ActionPlan | None = None)` — threaded from the delegation path when the turn is plan-bound. If `plan` is present and `status == approved`: allowed iff `(tool.name, normalized arguments)` exactly matches a listed call (normalize: drop `jit_token` key, sort keys, `json.dumps` compare). Mismatch → do **not** execute; treat as denial-with-new-JIT (existing strict path) and mark the plan `deviated` + audit `plan.deviation`.
- Each executed call writes its `Action` row with `plan_id` set (execution diff = the plan's calls vs. the `Action` rows).

- [ ] **Step 1:** Failing tests: listed call executes; reordered-but-identical args execute; changed arg value blocked; unlisted tool blocked; deviation audited; `plan_approval_mode="off"` → no enforcement.
- [ ] **Step 2:** Implement. Suite green.

### Task 4.3: Approval surfaces + UI

**Files:**
- Edit: `backend/vigilus/api/jit.py` (or a new `api/plans.py` — prefer new router `POST /api/plans/{id}/approve|deny`, `GET /api/plans`), `frontend/src/lib/api.ts`, `frontend/src/pages/Jit/index.tsx` (plans section) or `frontend/src/pages/Actions/`, Chat inline card component, `backend/vigilus/integrations/{gateway,telegram,discord}.py` (`plan:approve:<id>` / `plan:deny:<id>` callbacks reusing the Phase-3 allowlist check)
- Test: `backend/tests/test_plans_api.py` (new)

- [ ] **Step 1:** API tests (approve/deny/404/non-pending 400; audit event; resume triggered).
- [ ] **Step 2:** Backend routes + channel callbacks (generalize the JIT resolver to `resolve_approval(kind, id, ...)`; keep the old `jit:` callback data working).
- [ ] **Step 3:** Frontend: pending-plans list with the call table (tool, args, target), approve/deny buttons; Chat inline card mirrors the JIT card.
- [ ] **Step 4:** Full suite + ruff + frontend build/lint green.

### Task 4.4: End-to-end acceptance pass

- [ ] Manual: strict-trust operator, chat "restart the nginx container" → plan card → approve → only the listed `docker_restart` call runs → verification-style reply. Same via Telegram with allowlisted and non-allowlisted accounts. Kill the backend while parked, restart, approve → run resumes.
- [ ] `backend/.venv/bin/pytest -q` and `backend/.venv/bin/ruff check .` clean; `cd frontend && npm run build && npm run lint` clean.

---

## Rollout / compatibility notes

- `jit_park_resume` kill-switch reverts to the (retained) polling wait path without redeploying an older build.
- `_wait_for_jit_resolution` and its tests remain until one release after #41 ships, then a follow-up cleanup removes them (note it in the #41 PR description).
- Channel callback data changes are backward compatible: old `jit:approve:<id>` / `jit:deny:<id>` payloads keep resolving (mapped to `scope="default"`); stale buttons rendered before the upgrade still work.
- `Action.plan_id` is nullable — all existing rows unaffected; no backfill needed.
