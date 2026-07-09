import type {
  AuthUser,
  Provider,
  CreateProvider,
  UpdateProvider,
  Operator,
  CreateOperator,
  UpdateOperator,
  Tool,
  CreateTool,
  UpdateTool,
  McpServer,
  CreateMcpServer,
  UpdateMcpServer,
  Server,
  Credential,
  CreateCredential,
  Session,
  Message,
  SendMessage,
  Action,
  JitRequest,
  ScheduledTask,
  CreateScheduledTask,
  UpdateScheduledTask,
  Memory,
  CreateMemory,
  RunningTask,
  RunningTaskDetail,
  CommandSpec,
  CommandResult,
  ProviderCatalogEntry,
  ChannelConfig,
  ChannelConfigUpsert,
  ChannelAccount,
  ChannelPlatform,
  SearchConfig,
  SearchConfigUpsert,
  SearchTestResult,
  ScopeOverview,
  ScopeHostNode,
  ScopeTimeseriesPoint,
  ScopeSeverityBucket,
  ScopePortBucket,
  ScopeHostDetail,
  ScopeInventoryHost,
  ScopeDeleteResult,
  ScopePromoteResult,
  ScopeNetworkRole,
  ScopeNetworkRoleUpdate,
  ScopeSegment,
  ScopeSegmentUpdate,
  UpdateStatus,
} from '@/types';

export interface OrchestratorConfig {
  provider_id: string | null;
  model: string | null;
  system_prompt: string;
  custom_identity?: string | null;
  soul?: string | null;
  timezone?: string;
}

export interface OrchestratorConfigUpdate {
  provider_id?: string | null;
  model?: string | null;
  system_prompt?: string | null;
  custom_identity?: string | null;
  soul?: string | null;
  timezone?: string;
}

// ─── Fetch wrapper ────────────────────────────────────────────────────────────

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl = '/api') {
    this.baseUrl = baseUrl;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    const response = await fetch(url, { ...options, headers });

    if (!response.ok) {
      const error = await response.json().catch(() => ({
        detail: response.statusText,
        status_code: response.status,
      }));
      if (
        response.status === 401 &&
        !path.startsWith('/auth/login') &&
        !path.startsWith('/auth/setup') &&
        !path.startsWith('/auth/me')
      ) {
        window.dispatchEvent(new Event('vigilus:unauthorized'));
      }
      throw new ApiError(error.detail || 'Request failed', response.status);
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return response.json();
  }

  private get<T>(path: string) {
    return this.request<T>(path, { method: 'GET' });
  }

  private post<T>(path: string, body?: unknown) {
    return this.request<T>(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  private patch<T>(path: string, body: unknown) {
    return this.request<T>(path, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  }

  private put<T>(path: string, body: unknown) {
    return this.request<T>(path, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  private delete<T>(path: string) {
    return this.request<T>(path, { method: 'DELETE' });
  }

  // ─── Auth ─────────────────────────────────────────────────────────────
  getAuthSetupStatus() { return this.get<{ needs_setup: boolean }>('/auth/setup'); }
  setupFirstUser(data: { username: string; password: string }) { return this.post<AuthUser>('/auth/setup', data); }
  login(data: { username: string; password: string }) { return this.post<AuthUser>('/auth/login', data); }
  logout() { return this.post<void>('/auth/logout'); }
  getMe() { return this.get<AuthUser>('/auth/me'); }
  changePassword(data: { current_password: string; new_password: string }) {
    return this.post<void>('/auth/change-password', data);
  }

  // ─── Providers ────────────────────────────────────────────────────────

  listProviders() {
    return this.get<Provider[]>('/providers');
  }

  getDefaultProvider() {
    return this.get<Provider | null>('/providers/default');
  }

  getProvider(id: string) {
    return this.get<Provider>(`/providers/${id}`);
  }

  createProvider(data: CreateProvider) {
    return this.post<Provider>('/providers', data);
  }

  updateProvider(id: string, data: UpdateProvider) {
    return this.patch<Provider>(`/providers/${id}`, data);
  }

  deleteProvider(id: string) {
    return this.delete<void>(`/providers/${id}`);
  }

  testProvider(id: string) {
    return this.post<{ ok: boolean; models?: string[]; error?: string }>(`/providers/${id}/test`);
  }

  fetchOpenRouterModels() {
    return this.get<{ models: { id: string; name: string; context_length: number; pricing: { prompt: string; completion: string } }[] }>('/providers/openrouter/models');
  }

  // ─── Operators ────────────────────────────────────────────────────────

  listOperators() {
    return this.get<Operator[]>('/operators');
  }

  getOperator(id: string) {
    return this.get<Operator>(`/operators/${id}`);
  }

  createOperator(data: CreateOperator) {
    return this.post<Operator>('/operators', data);
  }

  updateOperator(id: string, data: UpdateOperator) {
    return this.patch<Operator>(`/operators/${id}`, data);
  }

  deleteOperator(id: string) {
    return this.delete<void>(`/operators/${id}`);
  }

  testOperator(id: string, prompt: string) {
    return this.post<{ok: boolean, messages?: any[], error?: string}>(`/operators/${id}/test`, { prompt });
  }

  // ─── Tools ────────────────────────────────────────────────────────────

  listTools() {
    return this.get<Tool[]>('/tools');
  }

  getTool(id: string) {
    return this.get<Tool>(`/tools/${id}`);
  }

  createTool(data: CreateTool) {
    return this.post<Tool>('/tools', data);
  }

  updateTool(id: string, data: UpdateTool) {
    return this.patch<Tool>(`/tools/${id}`, data);
  }

  deleteTool(id: string) {
    return this.delete<void>(`/tools/${id}`);
  }

  // ─── MCP Servers ──────────────────────────────────────────────────────

  listMcpServers() {
    return this.get<McpServer[]>('/mcp-servers');
  }

  getMcpServer(id: string) {
    return this.get<McpServer>(`/mcp-servers/${id}`);
  }

  createMcpServer(data: CreateMcpServer) {
    return this.post<McpServer>('/mcp-servers', data);
  }

  updateMcpServer(id: string, data: UpdateMcpServer) {
    return this.patch<McpServer>(`/mcp-servers/${id}`, data);
  }

  deleteMcpServer(id: string) {
    return this.delete<void>(`/mcp-servers/${id}`);
  }

  startMcpServer(id: string) {
    return this.post<McpServer>(`/mcp-servers/${id}/start`);
  }

  stopMcpServer(id: string) {
    return this.post<McpServer>(`/mcp-servers/${id}/stop`);
  }

  reinstallMcpServer(id: string) {
    return this.post<McpServer>(`/mcp-servers/${id}/reinstall`);
  }

  async testMcpServer(id: string) {
    return this.post<{status: string, tools: any[]}>(`/mcp-servers/${id}/test`);
  }

  importMcpServers(config: string) {
    return this.post<{ created: McpServer[]; skipped: string[]; errors: string[] }>(
      '/mcp-servers/import',
      { config },
    );
  }

  assignMcpServerTools(id: string, operatorIds: string[]) {
    return this.post<{ ok: boolean; tools: number; operators: number; assigned: number }>(
      `/mcp-servers/${id}/assign-tools`,
      { operator_ids: operatorIds },
    );
  }

  // ─── Servers ──────────────────────────────────────────────────────────────────
  async listServers() {
    return this.get<Server[]>('/servers');
  }

  async getServer(id: string) {
    return this.get<Server>(`/servers/${id}`);
  }

  async createServer(data: Partial<Server>) {
    return this.post<Server>('/servers', data);
  }

  async updateServer(id: string, data: Partial<Server>) {
    return this.patch<Server>(`/servers/${id}`, data);
  }

  async deleteServer(id: string) {
    return this.delete<void>(`/servers/${id}`);
  }

  // ─── Credentials ──────────────────────────────────────────────────────

  listCredentials() {
    return this.get<Credential[]>('/credentials');
  }

  createCredential(data: CreateCredential) {
    return this.post<Credential>('/credentials', data);
  }

  updateCredential(id: string, data: Partial<CreateCredential>) {
    return this.put<Credential>(`/credentials/${id}`, data);
  }

  deleteCredential(id: string) {
    return this.delete<void>(`/credentials/${id}`);
  }

  // ─── Sessions ─────────────────────────────────────────────────────────

  listSessions(params?: { operator_id?: string; status?: string }) {
    const query = params
      ? '?' + new URLSearchParams(params as Record<string, string>).toString()
      : '';
    return this.get<Session[]>(`/sessions${query}`);
  }

  getSession(id: string) {
    return this.get<Session>(`/sessions/${id}`);
  }

  createSession(operatorId: string) {
    return this.post<Session>('/sessions', { operator_id: operatorId });
  }

  updateSession(id: string, data: { title?: string; operator_id?: string }) {
    return this.patch<Session>(`/sessions/${id}`, data);
  }

  deleteSession(id: string) {
    return this.delete<void>(`/sessions/${id}`);
  }

  // ─── Messages ─────────────────────────────────────────────────────────

  listMessages(sessionId: string) {
    return this.get<Message[]>(`/sessions/${sessionId}/messages`);
  }

  sendMessage(sessionId: string, data: SendMessage) {
    return this.post<Message>(`/sessions/${sessionId}/messages`, data);
  }

  // ─── Actions ──────────────────────────────────────────────────────────

  listActions(params?: { status?: string; offset?: number; limit?: number }) {
    const query = params
      ? '?' + new URLSearchParams(params as unknown as Record<string, string>).toString()
      : '';
    return this.get<Action[]>(`/actions${query}`);
  }

  getAction(id: string) {
    return this.get<Action>(`/actions/${id}`);
  }

  approveAction(id: string) {
    return this.post<Action>(`/actions/${id}/approve`);
  }

  denyAction(id: string) {
    return this.post<Action>(`/actions/${id}/deny`);
  }

  // ─── JIT Requests ─────────────────────────────────────────────────────

  listJitRequests(params?: { status?: string }) {
    const query = params
      ? '?' + new URLSearchParams(params as Record<string, string>).toString()
      : '';
    return this.get<JitRequest[]>(`/jit${query}`);
  }

  getJitRequest(id: string) {
    return this.get<JitRequest>(`/jit/${id}`);
  }

  approveJitRequest(
    id: string,
    opts?: { ttl_minutes?: number | null; single_use?: boolean; resource?: string | null },
  ) {
    return this.post<JitRequest>(`/jit/${id}/approve`, opts ?? {});
  }

  denyJitRequest(id: string) {
    return this.post<JitRequest>(`/jit/${id}/deny`);
  }
  
  // ─── Metrics ────────────────────────────────────────────────────────
  
  getMetrics() {
    return this.get<any>('/system/metrics');
  }

  // ─── Updates ────────────────────────────────────────────────────────

  getUpdateStatus() {
    return this.get<UpdateStatus>('/system/update');
  }

  checkForUpdate() {
    return this.post<UpdateStatus>('/system/update/check');
  }

  // ─── Scheduled Tasks ────────────────────────────────────

  listSchedules() {
    return this.get<ScheduledTask[]>('/schedules');
  }

  createSchedule(data: CreateScheduledTask) {
    return this.post<ScheduledTask>('/schedules', data);
  }

  updateSchedule(id: string, data: UpdateScheduledTask) {
    return this.patch<ScheduledTask>(`/schedules/${id}`, data);
  }

  deleteSchedule(id: string) {
    return this.delete<{ ok: boolean }>(`/schedules/${id}`);
  }

  runScheduleNow(id: string) {
    return this.post<ScheduledTask>(`/schedules/${id}/run`);
  }

  // ─── Orchestrator ───────────────────────────────────────

  getOrchestratorConfig() {
    return this.get<OrchestratorConfig>('/orchestrator');
  }

  updateOrchestratorConfig(data: OrchestratorConfigUpdate) {
    return this.patch<OrchestratorConfig>('/orchestrator', data);
  }

  // ─── Memories ───────────────────────────────────────────

  listMemories(scope?: string) {
    const query = scope ? `?scope=${encodeURIComponent(scope)}` : '';
    return this.get<Memory[]>(`/memories${query}`);
  }

  createMemory(data: CreateMemory) {
    return this.post<Memory>('/memories', data);
  }

  updateMemory(id: string, data: { content?: string; category?: string | null }) {
    return this.patch<Memory>(`/memories/${id}`, data);
  }

  deleteMemory(id: string) {
    return this.delete<{ ok: boolean }>(`/memories/${id}`);
  }

  // ─── Running (live) tasks ───────────────────────────────

  listRunningTasks() {
    return this.get<RunningTask[]>('/running-tasks');
  }

  getRunningTask(sessionId: string) {
    return this.get<RunningTaskDetail>(`/running-tasks/${sessionId}`);
  }

  cancelRunningTask(sessionId: string) {
    return this.post<{ ok: boolean; session_id: string }>(`/running-tasks/${sessionId}/cancel`);
  }

  // ─── Commands ────────────────────────────────────────────

  listCommands() {
    return this.get<CommandSpec[]>('/commands');
  }

  runCommand(req: { command: string; args?: string; session_id?: string | null }) {
    return this.post<CommandResult>('/commands/run', req);
  }

  // ─── Provider catalog ────────────────────────────────────

  getProviderCatalog() {
    return this.get<{ catalog: ProviderCatalogEntry[] }>('/providers/catalog');
  }

  // ─── Channels (Telegram / Discord) ──────────────────────

  listChannelConfigs() {
    return this.get<ChannelConfig[]>('/channels');
  }

  upsertChannelConfig(platform: ChannelPlatform, data: ChannelConfigUpsert) {
    return this.put<ChannelConfig>(`/channels/${platform}`, data);
  }

  deleteChannelConfig(platform: ChannelPlatform) {
    return this.delete<ChannelConfig>(`/channels/${platform}`);
  }

  testChannel(platform: ChannelPlatform) {
    return this.post<{ ok: boolean; bot_username?: string | null; error?: string | null }>(
      `/channels/${platform}/test`
    );
  }

  listChannelAccounts() {
    return this.get<ChannelAccount[]>('/channels/accounts');
  }

  upsertChannelAccount(data: {
    platform: ChannelPlatform;
    external_user_id: string;
    allowed: boolean;
    label?: string | null;
  }) {
    return this.post<ChannelAccount>('/channels/accounts', data);
  }

  deleteChannelAccount(id: string) {
    return this.delete<void>(`/channels/accounts/${id}`);
  }

  // ─── Search / research (Vigilus-only) ──────────────────────

  getSearchConfig() {
    return this.get<SearchConfig>('/search/config');
  }

  upsertSearchConfig(data: SearchConfigUpsert) {
    return this.put<SearchConfig>('/search/config', data);
  }

  testSearch() {
    return this.post<SearchTestResult>('/search/test');
  }

  // ─── Scope (network attack surface) ───────────────────────────────────

  scopeOverview() {
    return this.get<ScopeOverview>('/scope/overview');
  }

  scopeHosts() {
    return this.get<ScopeHostNode[]>('/scope/hosts');
  }

  scopeFindingsTimeseries(days = 30) {
    return this.get<ScopeTimeseriesPoint[]>(`/scope/findings/timeseries?days=${days}`);
  }

  scopeFindingsSeverity() {
    return this.get<ScopeSeverityBucket[]>('/scope/findings/severity');
  }

  scopePortsDistribution() {
    return this.get<ScopePortBucket[]>('/scope/ports/distribution');
  }

  scopeHostDetail(identity: string) {
    return this.get<ScopeHostDetail>(`/scope/host/${encodeURIComponent(identity)}`);
  }

  scopeInventory() {
    return this.get<ScopeInventoryHost[]>('/scope/inventory');
  }

  scopeDeleteHosts(ips: string[]) {
    return this.post<ScopeDeleteResult>('/scope/inventory/delete', { ips });
  }

  scopePromoteHosts(ips: string[], credentialId?: string | null) {
    return this.post<ScopePromoteResult>('/scope/hosts/promote', {
      ips,
      credential_id: credentialId || null,
    });
  }

  scopeSegments() {
    return this.get<ScopeSegment[]>('/scope/segments');
  }

  scopeSetSegment(payload: ScopeSegmentUpdate) {
    return this.post<ScopeSegment>('/scope/segments', payload);
  }

  scopeSetHostRole(payload: ScopeNetworkRoleUpdate) {
    return this.post<ScopeNetworkRole>('/scope/hosts/role', payload);
  }
}

// ─── Error class ──────────────────────────────────────────────────────────────

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// ─── Singleton export ─────────────────────────────────────────────────────────

export const api = new ApiClient();
