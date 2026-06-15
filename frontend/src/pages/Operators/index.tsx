import { useState, useEffect, useRef, useCallback } from 'react';
import { Bot, Plus, X, Play, Settings, Shield, Trash2, Edit2, Search, Loader2, CircleDot, Activity } from 'lucide-react';
import { api } from '@/lib/api';
import { Operator, Provider, Tool, PermissionLevel, TrustMode, RunningTask } from '@/types';
import { useToast, useConfirm } from '@/components/Notifications';
import { MemoryPanel } from '@/components/MemoryPanel';
import { buildOperatorStatus, statusFor } from '@/lib/operatorStatus';
import { OperatorActivityDrawer } from './OperatorActivityDrawer';

const EMPTY_FORM = {
  name: '',
  description: '',
  systemPrompt: 'You are a helpful infrastructure operator.',
  soul: '',
  providerId: '',
  modelOverride: false,
  model: '',
  permission: 'read' as PermissionLevel,
  trustMode: 'inherit' as TrustMode,
  workingDir: '/tmp',
  selectedTools: [] as string[],
  enabled: true,
};

export default function Operators() {
  const toast = useToast();
  const confirm = useConfirm();
  const [operators, setOperators] = useState<Operator[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);

  // Live status
  const [runningTasks, setRunningTasks] = useState<RunningTask[]>([]);
  const [activityOpId, setActivityOpId] = useState<string | null>(null);
  const statusMap = buildOperatorStatus(runningTasks);

  // Modals
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [testOperatorId, setTestOperatorId] = useState<string | null>(null);

  // Form State
  const [form, setForm] = useState(EMPTY_FORM);

  // OpenRouter model catalog for operator model override
  const [orModels, setOrModels] = useState<{ id: string; name: string; context_length: number; pricing: { prompt: string; completion: string } }[]>([]);
  const [, setOrModelsLoading] = useState(false);
  const [orModelSearch, setOrModelSearch] = useState('');
  const [orDropdownOpen, setOrDropdownOpen] = useState(false);
  const orDropdownRef = useRef<HTMLDivElement>(null);

  const fetchOpenRouterModels = useCallback(async () => {
    setOrModelsLoading(true);
    try {
      const data = await api.fetchOpenRouterModels();
      setOrModels(data.models ?? []);
    } catch { /* ignore */ } finally {
      setOrModelsLoading(false);
    }
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (orDropdownRef.current && !orDropdownRef.current.contains(e.target as Node)) {
        setOrDropdownOpen(false);
        setOrModelSearch('');
      }
    };
    if (orDropdownOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [orDropdownOpen]);

  // Test State
  const [testPrompt, setTestPrompt] = useState('List the files in the current directory');
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [opsData, provData, toolsData] = await Promise.all([
        api.listOperators(),
        api.listProviders(),
        api.listTools()
      ]);
      setOperators(opsData);
      setProviders(provData);
      // web_search/web_fetch are reserved for the Vigilus orchestrator and are
      // not assignable to operators (enforced server-side too).
      setTools(toolsData.filter(t => t.name !== 'web_search' && t.name !== 'web_fetch'));
      if (provData.length > 0 && !form.providerId) {
        setForm(f => ({ ...f, providerId: provData[0].id }));
      }
    } catch (err) {
      console.error('Failed to fetch data', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  // Poll the live task registry so operator status (idle/running) stays current.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const tasks = await api.listRunningTasks();
        if (!cancelled) setRunningTasks(tasks);
      } catch { /* transient */ }
    };
    tick();
    const iv = setInterval(tick, 3000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const openCreate = () => {
    setEditingId(null);
    const defProvider = providers.find(p => p.is_default) ?? providers[0];
    setForm({
      ...EMPTY_FORM,
      providerId: defProvider?.id ?? '',
      model: defProvider?.default_model ?? '',
      modelOverride: false,
    });
    setIsModalOpen(true);
  };

  const openEdit = (op: Operator) => {
    setEditingId(op.id);
    const provider = providers.find(p => p.id === op.provider_id);
    // Override is on iff a specific model is pinned. A null model means
    // "follow the provider default" (resolved at runtime).
    setForm({
      name: op.name,
      description: op.description,
      systemPrompt: op.system_prompt ?? 'You are a helpful infrastructure operator.',
      soul: op.soul ?? '',
      providerId: op.provider_id ?? providers[0]?.id ?? '',
      modelOverride: !!op.model,
      model: op.model ?? provider?.default_model ?? '',
      permission: op.permission_level,
      trustMode: op.trust_mode,
      workingDir: op.working_dir ?? '/tmp',
      selectedTools: op.tool_ids,
      enabled: op.enabled,
    });
    setIsModalOpen(true);
  };

  const closeModal = () => {
    setIsModalOpen(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Override off → store null so the operator follows its provider's default
    // model at runtime (resolved by OperatorRuntime), rather than pinning it.
    const effectiveModel = form.modelOverride ? (form.model.trim() || null) : null;
    try {
      if (editingId) {
        await api.updateOperator(editingId, {
          name: form.name,
          description: form.description,
          system_prompt: form.systemPrompt,
          soul: form.soul,
          provider_id: form.providerId || undefined,
          model: effectiveModel,
          permission_level: form.permission,
          trust_mode: form.trustMode,
          working_dir: form.workingDir || null,
          tool_ids: form.selectedTools,
          enabled: form.enabled,
        });
        toast('Operator updated', 'success');
      } else {
        await api.createOperator({
          name: form.name,
          description: form.description,
          system_prompt: form.systemPrompt,
          soul: form.soul || null,
          provider_id: form.providerId,
          model: effectiveModel,
          permission_level: form.permission,
          trust_mode: form.trustMode,
          working_dir: form.workingDir,
          tool_ids: form.selectedTools,
        });
        toast('Operator created', 'success');
      }
      closeModal();
      fetchData();
    } catch (err: any) {
      toast(`Failed to ${editingId ? 'update' : 'create'} operator: ${err.message}`, 'error');
    }
  };

  const handleDelete = async (op: Operator) => {
    const ok = await confirm({
      title: 'Delete operator?',
      message: `Are you sure you want to delete "${op.name}"? This action cannot be undone.`,
    });
    if (!ok) return;
    try {
      await api.deleteOperator(op.id);
      if (editingId === op.id) closeModal();
      toast('Operator deleted', 'success');
      fetchData();
    } catch (err: any) {
      toast(`Failed to delete operator: ${err.message}`, 'error');
    }
  };

  const toggleTool = (toolId: string) => {
    setForm(f => ({
      ...f,
      selectedTools: f.selectedTools.includes(toolId)
        ? f.selectedTools.filter(id => id !== toolId)
        : [...f.selectedTools, toolId],
    }));
  };

  const runTest = async () => {
    if (!testOperatorId) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testOperator(testOperatorId, testPrompt);
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, error: String(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[20px] font-medium text-text-primary dark:text-text-primary tracking-[-0.02em]">
            Operators
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mt-1">
            Build and manage specialized AI agents to handle infrastructure tasks.
          </p>
        </div>
        <button
          onClick={openCreate}
          className="flex items-center px-3 py-1.5 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover transition-colors shadow-sm"
        >
          <Plus className="w-4 h-4 mr-1.5" />
          Create Operator
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {loading ? (
          <div className="col-span-full py-20 text-center text-sm text-text-secondary">Loading operators...</div>
        ) : operators.length === 0 ? (
          <div className="col-span-full flex flex-col items-center justify-center py-20 px-6 border border-border dark:border-border rounded-card bg-white dark:bg-surface">
            <div className="w-12 h-12 rounded-full bg-surface dark:bg-surface flex items-center justify-center mb-4">
              <Bot className="w-6 h-6 text-text-secondary/30 dark:text-text-secondary/30" strokeWidth={1.5} />
            </div>
            <p className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-1">
              No operators configured
            </p>
            <p className="text-[13px] text-text-secondary dark:text-text-secondary text-center max-w-[320px]">
              Create specialized operators by assigning them tools and a custom system prompt.
            </p>
          </div>
        ) : (
          operators.map(op => {
            const st = statusFor(statusMap, op.name);
            const defModel = providers.find(p => p.id === op.provider_id)?.default_model;
            const modelLabel = op.model ?? (defModel ? `Default (${defModel})` : 'Provider default');
            return (
            <div key={op.id} className="border border-border dark:border-border rounded-card bg-white dark:bg-surface overflow-hidden flex flex-col transition-shadow hover:shadow-sm">
              <div
                role="button"
                tabIndex={0}
                onClick={() => setActivityOpId(op.id)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActivityOpId(op.id); } }}
                className="p-5 flex-1 text-left w-full cursor-pointer hover:bg-surface/30 dark:hover:bg-border/20 transition-colors"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-accent/10 dark:bg-accent/20 flex items-center justify-center text-accent">
                      <Bot className="w-5 h-5" />
                    </div>
                    <div>
                      <h3 className="text-[15px] font-medium text-text-primary dark:text-text-primary">{op.name}</h3>
                      <p className="text-[12px] text-text-secondary dark:text-text-secondary">Model: {modelLabel}</p>
                    </div>
                  </div>
                  <span
                    className={
                      'inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded font-medium shrink-0 ' +
                      (st.running ? 'bg-info/15 text-info' : 'bg-surface text-text-secondary')
                    }
                  >
                    {st.running ? <Loader2 className="w-3 h-3 animate-spin" /> : <CircleDot className="w-3 h-3" />}
                    {st.running ? 'Running' : 'Idle'}
                  </span>
                </div>
                {st.running && st.currentStep && (
                  <p className="text-[12px] text-info mb-2 flex items-center gap-1.5">
                    <Activity className="w-3 h-3 shrink-0" />
                    <span className="truncate">{st.currentStep}</span>
                  </p>
                )}
                <p className="text-[13px] text-text-secondary dark:text-text-secondary mb-4 line-clamp-2 min-h-[40px]">
                  {op.description}
                </p>
                <div className="flex flex-wrap gap-2 mb-2">
                  <div className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border border-border dark:border-border bg-surface/50 dark:bg-surface/50 text-text-secondary">
                    <Shield className="w-3 h-3 mr-1" />
                    {op.permission_level}
                  </div>
                  <div className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border border-border dark:border-border bg-surface/50 dark:bg-surface/50 text-text-secondary">
                    <Settings className="w-3 h-3 mr-1" />
                    {op.tool_ids.length} tools
                  </div>
                  {op.is_builtin && (
                    <div className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border border-blue-200 bg-blue-50 text-blue-600 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-400">
                      Built-in
                    </div>
                  )}
                </div>
              </div>
              <div className="px-5 py-3 border-t border-border dark:border-border bg-surface/30 dark:bg-surface/30 flex justify-end gap-2">
                <button
                  onClick={() => setTestOperatorId(op.id)}
                  className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors flex items-center"
                >
                  <Play className="w-3.5 h-3.5 mr-1" /> Test
                </button>
                <button
                  onClick={() => openEdit(op)}
                  className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors flex items-center"
                >
                  <Edit2 className="w-3.5 h-3.5 mr-1" /> Edit
                </button>
                <button
                  onClick={() => handleDelete(op)}
                  className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-danger hover:bg-danger/5 transition-colors flex items-center"
                >
                  <Trash2 className="w-3.5 h-3.5 mr-1" /> Delete
                </button>
              </div>
            </div>
            );
          })
        )}
      </div>

      {/* Create / Edit Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-surface border border-border dark:border-border rounded-card w-full max-w-2xl shadow-xl flex flex-col max-h-[90vh]">
            <div className="flex items-center justify-between px-6 py-4 border-b border-border dark:border-border">
              <h2 className="text-[16px] font-medium text-text-primary dark:text-text-primary">
                {editingId ? 'Edit Operator' : 'Create Operator'}
              </h2>
              <button onClick={closeModal} className="text-text-secondary hover:text-text-primary">
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
              <div className="px-6 py-5 overflow-y-auto flex-1 space-y-6">

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Name</label>
                    <input
                      required
                      value={form.name}
                      onChange={e => setForm({ ...form, name: e.target.value })}
                      placeholder="e.g. SRE Assistant"
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Description</label>
                    <input
                      required
                      value={form.description}
                      onChange={e => setForm({ ...form, description: e.target.value })}
                      placeholder="What is this operator for?"
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent"
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">System Prompt</label>
                  <textarea
                    required
                    value={form.systemPrompt}
                    onChange={e => setForm({ ...form, systemPrompt: e.target.value })}
                    rows={4}
                    className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent font-mono"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Soul</label>
                  <p className="text-[11px] text-text-secondary/70">
                    Optional persona — tone, values, and quirks carried into every task this operator runs.
                  </p>
                  <textarea
                    value={form.soul}
                    onChange={e => setForm({ ...form, soul: e.target.value })}
                    rows={3}
                    placeholder="e.g. You are meticulous and a little paranoid — always double-check before changing anything, and say so when you're unsure."
                    className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent placeholder:text-text-secondary/40"
                  />
                </div>

                {editingId && (
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Memory</label>
                    <p className="text-[11px] text-text-secondary/70">
                      Facts this operator has learned (shared environment knowledge plus its private notes).
                      Saved immediately — independent of the form below.
                    </p>
                    <MemoryPanel
                      scopes={['global', editingId]}
                      privateScopeLabel={form.name || 'This operator'}
                    />
                  </div>
                )}

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Provider</label>
                    <select
                      required
                      value={form.providerId}
                      onChange={e => {
                        const newProviderId = e.target.value;
                        const newProvider = providers.find(p => p.id === newProviderId);
                        setForm({
                          ...form,
                          providerId: newProviderId,
                          modelOverride: false,
                          model: newProvider?.default_model ?? '',
                        });
                        // Pre-fetch OpenRouter models if needed
                        if (newProvider?.type === 'openrouter' && orModels.length === 0) {
                          fetchOpenRouterModels();
                        }
                      }}
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent"
                    >
                      {providers.map(p => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.type}){p.is_default ? ' ★' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Model</label>
                      <label className="flex items-center gap-1.5 text-[11px] text-text-secondary cursor-pointer">
                        <input
                          type="checkbox"
                          checked={form.modelOverride}
                          onChange={e => {
                            const override = e.target.checked;
                            if (!override) {
                              const provider = providers.find(p => p.id === form.providerId);
                              setForm({ ...form, modelOverride: false, model: provider?.default_model ?? '' });
                            } else {
                              setForm({ ...form, modelOverride: true });
                              const provider = providers.find(p => p.id === form.providerId);
                              if (provider?.type === 'openrouter' && orModels.length === 0) fetchOpenRouterModels();
                            }
                          }}
                          className="rounded border-border text-accent focus:ring-accent"
                        />
                        Override
                      </label>
                    </div>
                    {!form.modelOverride ? (
                      <div className="px-3 py-2 text-[13px] bg-surface/50 border border-border dark:border-border rounded-md text-text-secondary">
                        {form.model || <span className="italic opacity-50">No default model set</span>}
                        <span className="text-[11px] ml-2 opacity-60">(from provider)</span>
                      </div>
                    ) : (() => {
                      const selectedProvider = providers.find(p => p.id === form.providerId);
                      if (selectedProvider?.type === 'openrouter') {
                        return (
                          <div className="relative" ref={orDropdownRef}>
                            <div className="flex">
                              <div className="relative flex-1">
                                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-secondary pointer-events-none" />
                                <input
                                  type="text"
                                  value={orDropdownOpen ? orModelSearch : (form.model || 'openrouter/auto')}
                                  onChange={e => { setOrModelSearch(e.target.value); setOrDropdownOpen(true); }}
                                  onFocus={() => { setOrModelSearch(form.model || ''); setOrDropdownOpen(true); }}
                                  placeholder="Search models..."
                                  className="w-full pl-7 pr-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-l-md focus:border-accent"
                                />
                              </div>
                              <button type="button" onClick={() => setOrDropdownOpen(p => !p)} className="px-2.5 py-2 bg-transparent border border-l-0 border-border dark:border-border rounded-r-md hover:bg-surface">
                                <svg className={`w-3.5 h-3.5 text-text-secondary transition-transform ${orDropdownOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
                              </button>
                            </div>
                            {orDropdownOpen && (
                              <div className="absolute z-50 mt-1 w-full bg-white dark:bg-surface border border-border dark:border-border rounded-md shadow-lg max-h-[200px] overflow-y-auto">
                                <button type="button" onClick={() => { setForm({ ...form, model: 'openrouter/auto' }); setOrDropdownOpen(false); setOrModelSearch(''); }} className={`w-full text-left px-3 py-2 text-[13px] hover:bg-accent/5 ${form.model === 'openrouter/auto' ? 'bg-accent/10 text-accent font-medium' : ''}`}><div className="font-medium">openrouter/auto</div><div className="text-[11px] text-text-secondary">Auto-select</div></button>
                                <div className="border-t border-border" />
                                {orModels.filter(m => { if (!orModelSearch) return true; const q = orModelSearch.toLowerCase(); return m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q); }).slice(0, 50).map(m => (
                                  <button key={m.id} type="button" onClick={() => { setForm({ ...form, model: m.id }); setOrDropdownOpen(false); setOrModelSearch(''); }} className={`w-full text-left px-3 py-1.5 text-[12px] hover:bg-accent/5 border-b border-border/50 last:border-0 ${form.model === m.id ? 'bg-accent/10 text-accent' : ''}`}>
                                    <div className="truncate">{m.id}</div>
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      }
                      return (
                        <input
                          value={form.model}
                          onChange={e => setForm({ ...form, model: e.target.value })}
                          placeholder="e.g. gpt-4o, claude-3-opus"
                          className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent"
                        />
                      );
                    })()}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Permission Level</label>
                    <select
                      value={form.permission}
                      onChange={e => setForm({ ...form, permission: e.target.value as PermissionLevel })}
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent"
                    >
                      <option value="read">Read (Safe)</option>
                      <option value="write">Write</option>
                      <option value="exec">Execute</option>
                      <option value="elevate">Elevate (Root)</option>
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Trust Mode</label>
                    <select
                      value={form.trustMode}
                      onChange={e => setForm({ ...form, trustMode: e.target.value as TrustMode })}
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent"
                    >
                      <option value="strict">Strict (JIT required)</option>
                      <option value="lenient">Lenient (Auto-approve JIT)</option>
                      <option value="inherit">Inherit from Global Settings</option>
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Working Directory (Sandbox)</label>
                    <input
                      value={form.workingDir}
                      onChange={e => setForm({ ...form, workingDir: e.target.value })}
                      placeholder="/tmp"
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent font-mono"
                    />
                  </div>
                  {editingId && (
                    <div className="space-y-1.5 flex items-end pb-2">
                      <label className="flex items-center text-[13px] text-text-primary">
                        <input
                          type="checkbox"
                          checked={form.enabled}
                          onChange={e => setForm({ ...form, enabled: e.target.checked })}
                          className="mr-2"
                        />
                        Enabled
                      </label>
                    </div>
                  )}
                </div>

                <div className="space-y-2">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Assigned Tools</label>
                  <div className="border border-border dark:border-border rounded-md p-2 max-h-[160px] overflow-y-auto space-y-1 bg-surface/30">
                    {tools.map(tool => (
                      <label key={tool.id} className="flex items-center gap-2 p-1.5 hover:bg-surface dark:hover:bg-border rounded cursor-pointer transition-colors">
                        <input
                          type="checkbox"
                          checked={form.selectedTools.includes(tool.id)}
                          onChange={() => toggleTool(tool.id)}
                          className="rounded border-border text-accent focus:ring-accent"
                        />
                        <span className="text-[13px] text-text-primary dark:text-text-primary">{tool.name}</span>
                        <span className="text-[11px] text-text-secondary ml-auto uppercase">{tool.required_permission}</span>
                      </label>
                    ))}
                  </div>
                </div>

              </div>
              <div className="px-6 py-4 border-t border-border dark:border-border flex justify-end gap-3 bg-surface/30 dark:bg-surface/30">
                <button type="button" onClick={closeModal} className="px-4 py-2 text-[13px] font-medium text-text-secondary hover:text-text-primary">
                  Cancel
                </button>
                <button type="submit" className="px-4 py-2 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover">
                  {editingId ? 'Save Changes' : 'Create Operator'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Test Panel */}
      {testOperatorId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-surface border border-border dark:border-border rounded-card w-full max-w-3xl shadow-xl flex flex-col h-[80vh]">
            <div className="flex items-center justify-between px-6 py-4 border-b border-border dark:border-border">
              <h2 className="text-[16px] font-medium text-text-primary dark:text-text-primary">
                Test Operator
              </h2>
              <button onClick={() => setTestOperatorId(null)} className="text-text-secondary hover:text-text-primary">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-hidden flex flex-col p-6 gap-4">
              <div className="flex gap-4">
                <input
                  value={testPrompt}
                  onChange={e => setTestPrompt(e.target.value)}
                  className="flex-1 px-4 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent"
                  placeholder="Enter a prompt to test this operator..."
                />
                <button
                  onClick={runTest}
                  disabled={testing}
                  className="px-6 py-2 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover disabled:opacity-50 flex items-center"
                >
                  {testing ? 'Running...' : <><Play className="w-4 h-4 mr-1.5" /> Run</>}
                </button>
              </div>

              <div className="flex-1 border border-border dark:border-border rounded-md bg-surface/30 p-4 overflow-y-auto space-y-4">
                {testResult && testResult.ok && testResult.messages.map((m: any, i: number) => (
                  <div key={i} className="space-y-1">
                    <div className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">
                      {m.role} {m.name ? `(${m.name})` : ''}
                    </div>
                    <pre className="text-[13px] text-text-primary whitespace-pre-wrap font-sans bg-white dark:bg-surface p-3 rounded border border-border dark:border-border">
                      {m.content}
                    </pre>
                  </div>
                ))}
                {testResult && !testResult.ok && (
                  <div className="text-red-600 bg-red-50 p-3 rounded border border-red-200">
                    <div className="font-bold mb-1">Error</div>
                    <div>{testResult.error}</div>
                  </div>
                )}
                {!testing && !testResult && (
                  <div className="text-[13px] text-text-secondary text-center py-20">
                    Enter a prompt and click Run to test the operator loop.
                  </div>
                )}
                {testing && (
                  <div className="text-[13px] text-accent text-center py-20 animate-pulse">
                    Executing Operator loop...
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Live activity drawer */}
      {activityOpId && (() => {
        const op = operators.find(o => o.id === activityOpId);
        if (!op) return null;
        return (
          <OperatorActivityDrawer
            operator={op}
            tasks={statusFor(statusMap, op.name).tasks}
            onClose={() => setActivityOpId(null)}
          />
        );
      })()}

    </div>
  );
}
