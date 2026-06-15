import { useState, useEffect } from 'react';
import { Wrench, Plus, X, Globe, Terminal, Box, Shield, ShieldAlert, Key, Trash2, Edit2 } from 'lucide-react';
import { api } from '@/lib/api';
import { Tool, ToolImplementationType, PermissionLevel, CreateTool, UpdateTool } from '@/types';
import { cn } from '@/lib/utils';
import { useToast, useConfirm } from '@/components/Notifications';

// ─── Helpers ──────────────────────────────────────────────────────────────────

const TYPE_OPTIONS: { value: ToolImplementationType; label: string }[] = [
  { value: 'native', label: 'Native (Python handler)' },
  { value: 'http', label: 'HTTP Endpoint' },
  { value: 'mcp', label: 'MCP Server' },
];

const PERMISSION_OPTIONS: { value: PermissionLevel; label: string }[] = [
  { value: 'read', label: 'Read' },
  { value: 'write', label: 'Write' },
  { value: 'exec', label: 'Execute' },
  { value: 'elevate', label: 'Elevate' },
];

const getTypeIcon = (type: ToolImplementationType) => {
  switch (type) {
    case 'http': return <Globe className="w-3.5 h-3.5 mr-1" />;
    case 'mcp': return <Box className="w-3.5 h-3.5 mr-1" />;
    case 'native': return <Terminal className="w-3.5 h-3.5 mr-1" />;
    default: return null;
  }
};

const getTypeColor = (type: ToolImplementationType) => {
  switch (type) {
    case 'http': return 'text-blue-600 bg-blue-50 dark:text-blue-400 dark:bg-blue-500/10 border-blue-200 dark:border-blue-500/20';
    case 'mcp': return 'text-purple-600 bg-purple-50 dark:text-purple-400 dark:bg-purple-500/10 border-purple-200 dark:border-purple-500/20';
    case 'native': return 'text-emerald-600 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-500/10 border-emerald-200 dark:border-emerald-500/20';
    default: return 'text-gray-600 bg-gray-50 border-gray-200';
  }
};

const getPermissionIcon = (perm: PermissionLevel) => {
  if (perm === 'elevate') return <ShieldAlert className="w-3.5 h-3.5 mr-1" />;
  if (perm === 'exec' || perm === 'write') return <Key className="w-3.5 h-3.5 mr-1" />;
  return <Shield className="w-3.5 h-3.5 mr-1" />;
};

const getPermissionColor = (perm: PermissionLevel) => {
  switch (perm) {
    case 'read': return 'text-gray-600 bg-gray-50 dark:text-gray-400 dark:bg-gray-500/10 border-gray-200 dark:border-gray-500/20';
    case 'write': return 'text-amber-600 bg-amber-50 dark:text-amber-400 dark:bg-amber-500/10 border-amber-200 dark:border-amber-500/20';
    case 'exec': return 'text-orange-600 bg-orange-50 dark:text-orange-400 dark:bg-orange-500/10 border-orange-200 dark:border-orange-500/20';
    case 'elevate': return 'text-red-600 bg-red-50 dark:text-red-400 dark:bg-red-500/10 border-red-200 dark:border-red-500/20';
    default: return 'text-gray-600 bg-gray-50 border-gray-200';
  }
};

// ─── Form state shape ─────────────────────────────────────────────────────────

interface ToolFormState {
  name: string;
  description: string;
  implementationType: ToolImplementationType;
  requiredPermission: PermissionLevel;
  nativeHandler: string;
  httpMethod: string;
  httpUrl: string;
  httpHeaders: string;
  httpBodyTemplate: string;
  available: boolean;
  inputSchema: string;
}

const EMPTY_FORM: ToolFormState = {
  name: '',
  description: '',
  implementationType: 'http',
  requiredPermission: 'read',
  nativeHandler: '',
  httpMethod: 'GET',
  httpUrl: '',
  httpHeaders: '{}',
  httpBodyTemplate: '',
  available: true,
  inputSchema: '{}',
};

function formToUpdatePayload(f: ToolFormState): UpdateTool {
  const payload: UpdateTool = {
    name: f.name,
    description: f.description || null,
    implementation_type: f.implementationType,
    required_permission: f.requiredPermission,
    available: f.available,
  };

  if (f.implementationType === 'native') {
    payload.native_handler = f.nativeHandler || null;
    payload.http_config = null;
  } else if (f.implementationType === 'http') {
    let parsedHeaders = {};
    try { parsedHeaders = JSON.parse(f.httpHeaders || '{}'); } catch { /* skip */ }
    payload.http_config = {
      method: f.httpMethod,
      url: f.httpUrl,
      headers: parsedHeaders,
      body_template: f.httpBodyTemplate || undefined,
    };
    payload.native_handler = null;
  }

  try { payload.input_schema = JSON.parse(f.inputSchema || '{}'); } catch { /* skip */ }

  return payload;
}

function formToCreatePayload(f: ToolFormState): CreateTool {
  const update = formToUpdatePayload(f);
  return {
    name: f.name,
    description: f.description || undefined,
    implementation_type: f.implementationType,
    required_permission: f.requiredPermission,
    available: f.available,
    native_handler: update.native_handler ?? null,
    http_config: update.http_config ?? null,
    input_schema: (update.input_schema as Record<string, unknown>) ?? {},
  };
}

function toolToFormState(tool: Tool): ToolFormState {
  const httpCfg = tool.http_config as Record<string, any> | null;
  return {
    name: tool.name,
    description: tool.description ?? '',
    implementationType: tool.implementation_type,
    requiredPermission: tool.required_permission,
    nativeHandler: tool.native_handler ?? '',
    httpMethod: httpCfg?.method ?? 'GET',
    httpUrl: httpCfg?.url ?? '',
    httpHeaders: httpCfg?.headers ? JSON.stringify(httpCfg.headers, null, 2) : '{}',
    httpBodyTemplate: httpCfg?.body_template ?? '',
    available: tool.available,
    inputSchema: tool.input_schema ? JSON.stringify(tool.input_schema, null, 2) : '{}',
  };
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function Tools() {
  const toast = useToast();
  const confirm = useConfirm();
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);

  // Modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ToolFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetchTools = async () => {
    try {
      setLoading(true);
      const data = await api.listTools();
      setTools(data);
    } catch (err) {
      console.error('Failed to fetch tools', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchTools(); }, []);

  const openCreate = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError('');
    setIsModalOpen(true);
  };

  const openEdit = (tool: Tool) => {
    setEditingId(tool.id);
    setForm(toolToFormState(tool));
    setFormError('');
    setIsModalOpen(true);
  };

  const closeModal = () => {
    setIsModalOpen(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError('');
    setSubmitting(true);

    // Validate JSON fields
    if (form.implementationType === 'http') {
      try { JSON.parse(form.httpHeaders || '{}'); } catch {
        setFormError('Headers must be valid JSON');
        setSubmitting(false);
        return;
      }
    }
    try { JSON.parse(form.inputSchema || '{}'); } catch {
      setFormError('Input Schema must be valid JSON');
      setSubmitting(false);
      return;
    }

    try {
      if (editingId) {
        await api.updateTool(editingId, formToUpdatePayload(form));
        toast('Tool updated', 'success');
      } else {
        await api.createTool(formToCreatePayload(form));
        toast('Tool created', 'success');
      }
      closeModal();
      fetchTools();
    } catch (err: any) {
      setFormError(err.message || `Failed to ${editingId ? 'update' : 'create'} tool`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (tool: Tool) => {
    const ok = await confirm({
      title: 'Delete tool?',
      message: `Are you sure you want to delete "${tool.name}"? Operators using this tool will lose access.`,
    });
    if (!ok) return;
    try {
      await api.deleteTool(tool.id);
      toast('Tool deleted', 'success');
      fetchTools();
    } catch (err: any) {
      toast(`Failed to delete tool: ${err.message}`, 'error');
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[20px] font-medium text-text-primary dark:text-text-primary tracking-[-0.02em]">
            Tools
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mt-1">
            Browse and manage tools available to your operators.
          </p>
        </div>
        <button
          onClick={openCreate}
          className="flex items-center px-3 py-1.5 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover transition-colors shadow-sm"
        >
          <Plus className="w-4 h-4 mr-1.5" />
          Add Tool
        </button>
      </div>

      {/* Tool Table */}
      <div className="border border-border rounded-card bg-white dark:bg-surface dark:border-border overflow-hidden">
        {loading ? (
          <div className="py-20 text-center text-sm text-text-secondary">Loading tools...</div>
        ) : tools.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 px-6">
            <div className="w-12 h-12 rounded-full bg-surface dark:bg-surface flex items-center justify-center mb-4">
              <Wrench className="w-6 h-6 text-text-secondary/30 dark:text-text-secondary/30" strokeWidth={1.5} />
            </div>
            <p className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-1">
              No tools registered
            </p>
            <p className="text-[13px] text-text-secondary dark:text-text-secondary text-center max-w-[320px]">
              Tools are automatically discovered from your MCP servers, or you can add custom tools.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border dark:border-border bg-surface/50 dark:bg-surface/50">
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Name</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Type</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Permission</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Description</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border dark:divide-border">
                {tools.map((tool) => (
                  <tr key={tool.id} className="hover:bg-surface/30 dark:hover:bg-border/30 transition-colors">
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-medium text-text-primary dark:text-text-primary">
                          {tool.name}
                        </span>
                        {tool.is_builtin && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-blue-200 bg-blue-50 text-blue-600 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-400 uppercase font-medium">
                            Built-in
                          </span>
                        )}
                        {!tool.available && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full border border-gray-300 bg-gray-50 text-gray-500 uppercase font-medium">
                            Disabled
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <div className={cn(
                        "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border",
                        getTypeColor(tool.implementation_type)
                      )}>
                        {getTypeIcon(tool.implementation_type)}
                        <span className="capitalize">{tool.implementation_type}</span>
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <div className={cn(
                        "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border",
                        getPermissionColor(tool.required_permission)
                      )}>
                        {getPermissionIcon(tool.required_permission)}
                        <span className="capitalize">{tool.required_permission}</span>
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <div className="text-[13px] text-text-secondary truncate max-w-md">
                        {tool.description || <span className="italic opacity-50">No description</span>}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => openEdit(tool)}
                          className="p-1.5 text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border rounded transition-colors"
                          title="Edit tool"
                        >
                          <Edit2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDelete(tool)}
                          className="p-1.5 text-text-secondary hover:text-danger hover:bg-danger/5 rounded transition-colors"
                          title="Delete tool"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create / Edit Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-surface border border-border dark:border-border rounded-card w-full max-w-lg shadow-xl overflow-hidden flex flex-col max-h-[90vh]">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border dark:border-border">
              <h2 className="text-[16px] font-medium text-text-primary dark:text-text-primary">
                {editingId ? 'Edit Tool' : 'Add Tool'}
              </h2>
              <button onClick={closeModal} className="text-text-secondary hover:text-text-primary transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
              <div className="px-5 py-4 overflow-y-auto flex-1 space-y-4">
                {formError && (
                  <div className="p-3 text-[13px] text-red-600 bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 rounded-md">
                    {formError}
                  </div>
                )}

                {/* Name */}
                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Tool Name</label>
                  <input
                    required
                    value={form.name}
                    onChange={e => setForm({ ...form, name: e.target.value })}
                    placeholder="e.g. create_github_issue"
                    className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors"
                  />
                </div>

                {/* Description */}
                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Description</label>
                  <textarea
                    value={form.description}
                    onChange={e => setForm({ ...form, description: e.target.value })}
                    placeholder="What does this tool do?"
                    rows={2}
                    className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors"
                  />
                </div>

                {/* Type + Permission */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Implementation Type</label>
                    <select
                      value={form.implementationType}
                      onChange={e => setForm({ ...form, implementationType: e.target.value as ToolImplementationType })}
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors"
                    >
                      {TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Required Permission</label>
                    <select
                      value={form.requiredPermission}
                      onChange={e => setForm({ ...form, requiredPermission: e.target.value as PermissionLevel })}
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors"
                    >
                      {PERMISSION_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </div>

                {/* Native handler */}
                {form.implementationType === 'native' && (
                  <div className="space-y-1.5">
                    <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Native Handler (Python import path)</label>
                    <input
                      value={form.nativeHandler}
                      onChange={e => setForm({ ...form, nativeHandler: e.target.value })}
                      placeholder="e.g. vigilus.tools.native.docker:docker_list"
                      className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors font-mono"
                    />
                  </div>
                )}

                {/* HTTP config */}
                {form.implementationType === 'http' && (
                  <>
                    <div className="flex gap-3">
                      <div className="space-y-1.5 w-1/3">
                        <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Method</label>
                        <select
                          value={form.httpMethod}
                          onChange={e => setForm({ ...form, httpMethod: e.target.value })}
                          className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors"
                        >
                          <option value="GET">GET</option>
                          <option value="POST">POST</option>
                          <option value="PUT">PUT</option>
                          <option value="PATCH">PATCH</option>
                          <option value="DELETE">DELETE</option>
                        </select>
                      </div>
                      <div className="space-y-1.5 flex-1">
                        <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">URL Endpoint</label>
                        <input
                          required
                          value={form.httpUrl}
                          onChange={e => setForm({ ...form, httpUrl: e.target.value })}
                          placeholder="https://api.example.com/v1/resource"
                          className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors"
                        />
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Headers (JSON)</label>
                      <textarea
                        value={form.httpHeaders}
                        onChange={e => setForm({ ...form, httpHeaders: e.target.value })}
                        placeholder='{"Authorization": "Bearer token"}'
                        rows={3}
                        className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors font-mono"
                      />
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Body Template (optional)</label>
                      <textarea
                        value={form.httpBodyTemplate}
                        onChange={e => setForm({ ...form, httpBodyTemplate: e.target.value })}
                        placeholder="Use {{variables}} to map tool inputs"
                        rows={3}
                        className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors font-mono"
                      />
                    </div>
                  </>
                )}

                {/* Input schema */}
                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Input Schema (JSON)</label>
                  <textarea
                    value={form.inputSchema}
                    onChange={e => setForm({ ...form, inputSchema: e.target.value })}
                    rows={4}
                    className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors font-mono"
                  />
                </div>

                {/* Available toggle (edit only) */}
                {editingId && (
                  <div className="flex items-center gap-2">
                    <label className="flex items-center text-[13px] text-text-primary cursor-pointer">
                      <input
                        type="checkbox"
                        checked={form.available}
                        onChange={e => setForm({ ...form, available: e.target.checked })}
                        className="mr-2"
                      />
                      Available
                    </label>
                  </div>
                )}
              </div>

              <div className="px-5 py-4 border-t border-border dark:border-border flex justify-end gap-3 bg-surface/30 dark:bg-surface/30">
                <button
                  type="button"
                  onClick={closeModal}
                  className="px-4 py-2 text-[13px] font-medium text-text-secondary hover:text-text-primary transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-sm flex items-center"
                >
                  {submitting ? 'Saving...' : editingId ? 'Save Changes' : 'Create Tool'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
