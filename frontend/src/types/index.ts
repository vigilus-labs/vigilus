// ─── Auth ─────────────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string;
  username: string;
  created_at: string;
  last_login_at: string | null;
}

// ─── Enums ────────────────────────────────────────────────────────────────────

export type ProviderType = 'anthropic' | 'openai' | 'openai_compat' | 'openrouter' | 'google' | 'custom';

export type ServerStatus = 'online' | 'offline' | 'unknown';

export type McpServerStatus = 'running' | 'stopped' | 'error';

export type SessionStatus = 'active' | 'idle' | 'ended' | 'error';

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export type ActionStatus = 'pending' | 'approved' | 'denied' | 'auto_approved' | 'expired';

export type ActionCategory = 'tool_call' | 'file_access' | 'network' | 'shell' | 'other';

export type JitRequestStatus = 'pending' | 'approved' | 'denied' | 'expired' | 'revoked';

export type CredentialType = 'api_key' | 'ssh_key' | 'password' | 'token';

export type SshAuthMethod = 'key' | 'password';

export type ToolImplementationType = 'native' | 'http' | 'mcp';

export type PermissionLevel = 'read' | 'write' | 'exec' | 'elevate';

export type ActionOutcome = 'pending' | 'success' | 'error' | 'denied';

export type TrustMode = 'strict' | 'lenient' | 'inherit';

// ─── Core Models ──────────────────────────────────────────────────────────────

export interface Provider {
  id: string;
  name: string;
  type: ProviderType;
  base_url: string | null;
  has_api_key: boolean;
  default_model: string | null;
  extra_headers: Record<string, string> | null;
  tool_calling_supported: boolean;
  enabled: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface Operator {
  id: string;
  name: string;
  description: string;
  provider_id: string | null;
  model: string | null;
  system_prompt: string | null;
  soul: string | null;
  trust_mode: TrustMode;
  permission_level: PermissionLevel;
  working_dir: string | null;
  tool_ids: string[];
  is_builtin: boolean;
  delegatable: boolean;
  enabled: boolean;
  icon: string | null;
  created_at: string;
  updated_at: string;
}

export interface Tool {
  id: string;
  name: string;
  description: string | null;
  input_schema: Record<string, unknown>;
  implementation_type: ToolImplementationType;
  required_permission: PermissionLevel;
  native_handler: string | null;
  http_config: Record<string, unknown> | null;
  mcp_server_id: string | null;
  mcp_tool_name: string | null;
  is_builtin: boolean;
  available: boolean;
  created_at: string;
}

export interface McpServer {
  id: string;
  name: string;
  command: string;
  args: string[];
  env_vars: Record<string, string>;
  transport: 'stdio' | 'sse';
  sse_url: string | null;
  autostart: boolean;
  status: McpServerStatus;
  github_url: string | null;
  install_command: string | null;
  working_dir: string | null;
  last_started_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface Server {
  id: string;
  name: string;
  hostname: string;
  port: number;
  os: string | null;
  os_version: string | null;
  tags: string[];
  credential_id: string | null;
  notes: string | null;
  ip: string | null;
  status: ServerStatus;
  last_seen: string | null;
  created_at: string;
  updated_at: string;
}

export interface Credential {
  id: string;
  name: string;
  type: CredentialType;
  ssh_auth_method: SshAuthMethod | null;
  username: string | null;
  has_secret: boolean;
  has_passphrase: boolean;
  created_at: string;
}

export interface Session {
  id: string;
  title: string | null;
  operator_context: string | null;
  operator_id: string | null;
  origin: string | null;  // web | telegram | discord | schedule
  created_at: string;
  last_active_at: string;
}

export interface Message {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string | Record<string, unknown>;
  operator_id: string | null;
  created_at: string;
}

export interface Action {
  id: string;
  event: string;
  actor: string;
  operator_id: string | null;
  tool_id: string | null;
  tool_name: string | null;
  server_id: string | null;
  args: Record<string, unknown> | null;
  outcome: ActionOutcome;
  error: string | null;
  duration_ms: number | null;
  session_id: string | null;
  created_at: string;
}

export interface JitRequest {
  id: string;
  operator_id: string;
  operator_name: string;
  resource: string;
  permission: string;
  task_description: string;
  status: JitRequestStatus;
  token_id: string | null;
  ttl_minutes: number;
  scope_mode?: string;
  requested_at: string;
  resolved_at: string | null;
  approved_by: string | null;
  created_at: string;
}

// ─── Create / Update DTOs ─────────────────────────────────────────────────────

export interface CreateProvider {
  name: string;
  type: ProviderType;
  base_url?: string | null;
  api_key?: string | null;
  default_model?: string | null;
  extra_headers?: Record<string, string> | null;
  tool_calling_supported?: boolean;
  enabled?: boolean;
  is_default?: boolean;
}

export interface UpdateProvider {
  name?: string;
  type?: ProviderType;
  base_url?: string | null;
  api_key?: string | null;
  default_model?: string | null;
  extra_headers?: Record<string, string> | null;
  tool_calling_supported?: boolean;
  enabled?: boolean;
  is_default?: boolean;
}

export interface CreateOperator {
  name: string;
  description: string;
  system_prompt?: string | null;
  soul?: string | null;
  provider_id?: string | null;
  model?: string | null;
  permission_level?: PermissionLevel;
  trust_mode?: TrustMode;
  working_dir?: string | null;
  tool_ids?: string[];
  icon?: string | null;
}

export interface UpdateOperator {
  name?: string;
  description?: string;
  system_prompt?: string | null;
  soul?: string | null;
  provider_id?: string | null;
  model?: string | null;
  permission_level?: PermissionLevel;
  trust_mode?: TrustMode;
  working_dir?: string | null;
  enabled?: boolean;
  delegatable?: boolean;
  tool_ids?: string[];
  icon?: string | null;
}

export interface CreateMcpServer {
  name: string;
  command: string;
  args?: string[];
  env_vars?: Record<string, string>;
  transport: 'stdio' | 'sse';
  sse_url?: string | null;
  autostart?: boolean;
  github_url?: string | null;
  install_command?: string | null;
  working_dir?: string | null;
}

export interface UpdateMcpServer {
  name?: string;
  command?: string;
  args?: string[];
  env_vars?: Record<string, string>;
  transport?: 'stdio' | 'sse';
  sse_url?: string | null;
  autostart?: boolean;
  github_url?: string | null;
  install_command?: string | null;
  working_dir?: string | null;
}

export interface CreateCredential {
  name: string;
  type: CredentialType;
  ssh_auth_method?: SshAuthMethod | null;
  username?: string;
  secret: string;
  passphrase?: string;
}

export interface SendMessage {
  content: string;
}

export interface CreateTool {
  name: string;
  description?: string | null;
  input_schema?: Record<string, unknown>;
  implementation_type: ToolImplementationType;
  required_permission?: PermissionLevel;
  native_handler?: string | null;
  http_config?: Record<string, unknown> | null;
  mcp_server_id?: string | null;
  mcp_tool_name?: string | null;
  is_builtin?: boolean;
  available?: boolean;
}

export interface UpdateTool {
  name?: string;
  description?: string | null;
  input_schema?: Record<string, unknown> | null;
  implementation_type?: ToolImplementationType | null;
  required_permission?: PermissionLevel | null;
  native_handler?: string | null;
  http_config?: Record<string, unknown> | null;
  mcp_server_id?: string | null;
  mcp_tool_name?: string | null;
  available?: boolean | null;
}

// ─── Memories ─────────────────────────────────────────────────────────────────

// scope: 'global' (shared environment knowledge), 'orchestrator'
// (Vigilus-private), or an operator ID (operator-private).
export interface Memory {
  id: string;
  scope: string;
  content: string;
  category: string | null;
  source: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateMemory {
  scope?: string;
  content: string;
  category?: string | null;
}

// ─── API Response Wrappers ────────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface ApiError {
  detail: string;
  status_code: number;
}

// ─── WebSocket Events ─────────────────────────────────────────────────────────

export type WsEventType =
  | 'action.created'
  | 'action.updated'
  | 'action.completed'
  | 'jit.requested'
  | 'jit.resolved'
  | 'operator.stream'
  | 'server.status_changed'
  | 'mcp.server_status'
  | 'session.updated';

export interface WsEvent<T = unknown> {
  type: WsEventType;
  payload: T;
  timestamp?: string;
}

// ─── Running (live) tasks ─────────────────────────────────────────────────────

export interface RunningTask {
  id: string;
  session_id: string;
  title: string;
  started_at: string;
  elapsed_seconds: number;
  current_step: string;
  operator: string | null;
  cancelling: boolean;
}

export interface RunningTaskActivity {
  type: string;
  data: Record<string, any>;
  ts: string;
}

export interface RunningTaskDetail extends Partial<RunningTask> {
  running: boolean;
  activity: RunningTaskActivity[];
}

// ─── Commands ─────────────────────────────────────────────────────────────────

export interface CommandArg {
  name: string;
  description: string;
  optional: boolean;
}

export interface CommandSpec {
  name: string;
  summary: string;
  usage: string;
  args: CommandArg[];
  execution: 'server' | 'client';
  needs_session: boolean;
}

export type CommandResultKind =
  | 'markdown'
  | 'error'
  | 'session_created'
  | 'session_switch'
  | 'session_deleted'
  | 'config_changed'
  | 'stopped';

export interface CommandResult {
  kind: CommandResultKind;
  text: string;
  data: Record<string, unknown> | null;
}

// ─── Provider Catalog ─────────────────────────────────────────────────────────

export interface ProviderCatalogEntry {
  id: string;
  label: string;
  type: ProviderType;
  needs_api_key: boolean;
  needs_base_url: boolean;
  base_url: string | null;
  key_url: string | null;
  default_model: string | null;
}

// ─── Scheduled Tasks ──────────────────────────────────────────────────────────

export type ScheduleStatus = 'success' | 'error' | 'running' | 'skipped';

export interface ScheduleResult {
  status?: string;
  summary?: string;
  session_id?: string | null;
  error?: string;
}

export interface ScheduledTask {
  id: string;
  name: string;
  description: string | null;
  cron_expression: string;
  task_prompt: string;
  operator_id: string | null;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: ScheduleStatus | null;
  last_result: ScheduleResult | null;
  run_count: number;
  created_at: string;
  updated_at: string;
}

export interface CreateScheduledTask {
  name: string;
  description?: string | null;
  cron_expression: string;
  task_prompt: string;
  operator_id?: string | null;
  enabled?: boolean;
}

export interface UpdateScheduledTask {
  name?: string;
  description?: string | null;
  cron_expression?: string;
  task_prompt?: string;
  operator_id?: string | null;
  enabled?: boolean;
}

// ─── Channels ────────────────────────────────────────────────────────────────

export type ChannelPlatform = 'telegram' | 'discord';

export interface ChannelConfig {
  platform: ChannelPlatform;
  bot_username: string | null;
  enabled: boolean;
  respond_in_groups: boolean;
  default_operator_id: string | null;
  has_token: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ChannelConfigUpsert {
  bot_token?: string | null;
  bot_username?: string | null;
  enabled?: boolean;
  respond_in_groups?: boolean;
  default_operator_id?: string | null;
}

export interface ChannelAccount {
  id: string;
  platform: ChannelPlatform;
  external_user_id: string;
  allowed: boolean;
  label: string | null;
  user_id: string | null;
  created_at: string;
}

// ─── Search / research ─────────────────────────────────────────────────────

export type SearchBackendKind = 'searxng' | 'firecrawl';
export type FetchBackendKind = 'builtin' | 'firecrawl';

export interface SearchConfig {
  search_backend: SearchBackendKind;
  fetch_backend: FetchBackendKind;
  searxng_url: string | null;
  enabled: boolean;
  has_firecrawl_key: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SearchConfigUpsert {
  search_backend: SearchBackendKind;
  fetch_backend: FetchBackendKind;
  searxng_url?: string | null;
  firecrawl_api_key?: string | null;
  enabled: boolean;
}

export interface SearchTestResult {
  ok: boolean;
  backend?: string | null;
  result_count?: number | null;
  error?: string | null;
  hint?: string | null;
}

// ─── Scope ───────────────────────────────────────────────────────────────────

export interface ScopeOverview {
  managed: number;
  discovered_unique: number;
  unmanaged: number;
  open_ports: number;
  findings: number;
}

export type ScopeOrigin = 'managed' | 'discovered' | 'monitored';

export interface ScopeHostNode {
  id: string;
  label: string;
  ip: string | null;
  hostname: string | null;
  os: string | null;
  status: string | null;
  origins: ScopeOrigin[];
  managed: boolean;
  discovered_host_id: string | null;
  finding_count: number;
  open_port_count: number;
  monitored: boolean;
  segment: string | null;
  is_gateway: boolean;
  is_dns: boolean;
  is_switch: boolean;
  is_access_point: boolean;
  role_label: string | null;
}

export interface ScopeTimeseriesPoint {
  day: string;
  count: number;
}

export interface ScopeSeverityBucket {
  severity: string;
  count: number;
}

export interface ScopePortBucket {
  service: string;
  count: number;
}

export interface ScopePort {
  port: number;
  proto: string;
  state: string;
  service: string | null;
  product: string | null;
  version: string | null;
}

export interface ScopeFinding {
  id: string;
  source: string;
  kind: string;
  severity: string;
  title: string;
  detail: Record<string, unknown> | null;
  count: number;
  first_seen: string;
  last_seen: string;
}

export interface ScopeHostDetail {
  id: string;
  label: string;
  ip: string | null;
  hostname: string | null;
  os: string | null;
  origins: ScopeOrigin[];
  managed: boolean;
  monitored: boolean;
  ports: ScopePort[];
  findings: ScopeFinding[];
  recent_actions: { id: string; event: string; tool_name: string | null; outcome: string | null; created_at: string | null }[];
}

export interface ScopeInventoryHost {
  discovered_host_id: string;
  ip: string;
  hostname: string | null;
  mac: string | null;
  os: string | null;
  status: string | null;
  managed: boolean;
  open_port_count: number;
  finding_count: number;
  services: string[];
  scan_target: string | null;
  first_seen: string | null;
  last_seen: string | null;
}

export interface ScopeDeleteResult {
  deleted_ips: string[];
  deleted_hosts: number;
  deleted_services: number;
  deleted_findings: number;
}

export interface ScopePromoteResult {
  created: string[];
  already_managed: string[];
  invalid: string[];
}

export interface ScopeNetworkRole {
  ip: string;
  is_gateway: boolean;
  is_dns: boolean;
  is_switch: boolean;
  is_access_point: boolean;
  label: string | null;
  notes: string | null;
}

export interface ScopeNetworkRoleUpdate {
  ip: string;
  is_gateway: boolean;
  is_dns: boolean;
  is_switch: boolean;
  is_access_point: boolean;
  label?: string | null;
  notes?: string | null;
}

export interface ScopeSegment {
  cidr: string;
  label: string | null;
  color: string | null;
}

export interface ScopeSegmentUpdate {
  cidr: string;
  label?: string | null;
  color?: string | null;
}
