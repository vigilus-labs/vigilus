import { useState, useEffect, useCallback, useRef } from 'react';
import { Key, Sliders, Database, Plus, Trash2, Edit2, CheckCircle, XCircle, RefreshCw, Search, UserCog, Radio } from 'lucide-react';
import { api, ApiError } from '../../lib/api';
import { useToast, useConfirm } from '../../components/Notifications';
import { Provider, ProviderType, Credential, CredentialType, SshAuthMethod, ChannelConfig, ChannelAccount, ChannelPlatform } from '../../types';

const PROVIDER_TYPES: { value: ProviderType; label: string }[] = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'openai_compat', label: 'OpenAI-compatible' },
  { value: 'google', label: 'Google' },
  { value: 'custom', label: 'Custom' },
];

interface ProviderFormState {
  name: string;
  type: ProviderType;
  base_url: string;
  api_key: string;
  default_model: string;
  enabled: boolean;
}

const emptyForm: ProviderFormState = {
  name: '',
  type: 'anthropic',
  base_url: '',
  api_key: '',
  default_model: '',
  enabled: true,
};

function ProvidersTab() {
  const toast = useToast();
  const confirm = useConfirm();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  // null = form closed, '' = adding, otherwise the id of the provider being edited
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ProviderFormState>(emptyForm);
  const [saving, setSaving] = useState(false);

  // OpenRouter model catalog
  const [orModels, setOrModels] = useState<{ id: string; name: string; context_length: number; pricing: { prompt: string; completion: string } }[]>([]);
  const [orModelsLoading, setOrModelsLoading] = useState(false);
  const [orModelSearch, setOrModelSearch] = useState('');
  const [orDropdownOpen, setOrDropdownOpen] = useState(false);
  const orDropdownRef = useRef<HTMLDivElement>(null);

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

  const fetchOpenRouterModels = useCallback(async () => {
    setOrModelsLoading(true);
    try {
      const data = await api.fetchOpenRouterModels();
      setOrModels(data.models ?? []);
    } catch (err: any) {
      toast(`Failed to fetch OpenRouter models: ${err.message}`, 'error');
    } finally {
      setOrModelsLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchProviders();
  }, []);

  // Auto-fetch OpenRouter models when type is selected
  useEffect(() => {
    if (form.type === 'openrouter' && orModels.length === 0) {
      fetchOpenRouterModels();
    }
  }, [form.type, orModels.length, fetchOpenRouterModels]);

  const fetchProviders = async () => {
    try {
      const data = await api.listProviders();
      setProviders(data);
    } catch (err) {
      console.error('Failed to fetch providers', err);
    } finally {
      setLoading(false);
    }
  };

  const handleTest = async (id: string) => {
    try {
      const res = await api.testProvider(id);
      if (res.ok) {
        const models = res.models?.length ? ` Models: ${res.models.slice(0, 5).join(', ')}` : '';
        toast(`Connection successful!${models}`, 'success');
      } else {
        toast(`Connection failed: ${res.error}`, 'error');
      }
    } catch (err: any) {
      toast(`Test failed: ${err.message}`, 'error');
    }
  };

  const openAdd = () => {
    setForm(emptyForm);
    setEditingId('');
  };

  const openEdit = (p: Provider) => {
    setForm({
      name: p.name,
      type: p.type,
      base_url: p.type === 'openrouter' ? '' : (p.base_url ?? ''),
      api_key: '',
      default_model: p.default_model ?? '',
      enabled: p.enabled,
    });
    setEditingId(p.id);
  };

  const closeForm = () => {
    setEditingId(null);
    setForm(emptyForm);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const payload: Record<string, any> = {
        name: form.name,
        type: form.type,
        default_model: form.default_model || null,
        enabled: form.enabled,
      };
      // OpenRouter doesn't need base_url from the user
      if (form.type !== 'openrouter') {
        payload.base_url = form.base_url || null;
      }
      if (form.api_key) {
        payload.api_key = form.api_key;
      }

      if (editingId === '') {
        await api.createProvider(payload as any);
      } else if (editingId) {
        // Empty key means "leave unchanged" on edit; omit it from the payload
        if (!form.api_key) delete payload.api_key;
        await api.updateProvider(editingId, payload as any);
      }
      toast(editingId === '' ? 'Provider created' : 'Provider updated', 'success');
      closeForm();
      fetchProviders();
    } catch (err: any) {
      toast(`Failed to save provider: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: 'Delete provider?',
      message: 'Operators using this provider will stop working.',
    });
    if (!ok) return;
    try {
      await api.deleteProvider(id);
      if (editingId === id) closeForm();
      toast('Provider deleted', 'success');
      fetchProviders();
    } catch (err: any) {
      toast(`Failed to delete provider: ${err.message}`, 'error');
    }
  };

  if (loading) return <div className="text-sm text-text-secondary">Loading providers...</div>;

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-sm font-medium text-text-secondary">Configured Providers</h3>
        <button
          onClick={() => (editingId === null ? openAdd() : closeForm())}
          className="bg-white border border-border hover:bg-surface text-text-primary px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center"
        >
          <Plus className="w-4 h-4 mr-1.5" />
          Add Provider
        </button>
      </div>

      {editingId !== null && (
        <form onSubmit={handleSubmit} className="bg-surface border border-border rounded-md p-4 mb-6 space-y-4">
          <h4 className="text-sm font-medium text-text-primary">
            {editingId === '' ? 'New Provider' : 'Edit Provider'}
          </h4>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">Name</label>
              <input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">Type</label>
              <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value as ProviderType })} className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md">
                {PROVIDER_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>

            {/* Base URL — hidden for OpenRouter (auto-configured) */}
            {form.type !== 'openrouter' && (
              <div className="space-y-1.5">
                <label className="text-[12px] font-medium text-text-secondary uppercase">Base URL (optional)</label>
                <input value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} placeholder="https://api.example.com/v1" className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
              </div>
            )}

            {/* OpenRouter info banner */}
            {form.type === 'openrouter' && (
              <div className="space-y-1.5">
                <label className="text-[12px] font-medium text-text-secondary uppercase">Endpoint</label>
                <div className="px-3 py-2 text-[13px] bg-surface border border-border rounded-md text-text-secondary">
                  https://openrouter.ai/api/v1 <span className="text-[11px] ml-1">(auto-configured)</span>
                </div>
              </div>
            )}

            {/* Default Model — for non-OpenRouter, simple text input */}
            {form.type !== 'openrouter' && (
              <div className="space-y-1.5">
                <label className="text-[12px] font-medium text-text-secondary uppercase">Default Model (optional)</label>
                <input value={form.default_model} onChange={e => setForm({ ...form, default_model: e.target.value })} placeholder="claude-sonnet-4-6" className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
              </div>
            )}

            {/* OpenRouter model picker */}
            {form.type === 'openrouter' && (
              <div className="space-y-1.5">
                <label className="text-[12px] font-medium text-text-secondary uppercase">Default Model</label>
                <div className="flex gap-2 items-start">
                  <div className="flex-1 relative" ref={orDropdownRef}>
                    <div className="flex">
                      <div className="relative flex-1">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-secondary pointer-events-none" />
                        <input
                          type="text"
                          value={orDropdownOpen ? orModelSearch : (form.default_model || 'openrouter/auto')}
                          onChange={e => {
                            setOrModelSearch(e.target.value);
                            setOrDropdownOpen(true);
                          }}
                          onFocus={() => {
                            setOrModelSearch(form.default_model || '');
                            setOrDropdownOpen(true);
                          }}
                          placeholder="Search models..."
                          className="w-full pl-7 pr-3 py-2 text-[13px] bg-white border border-border rounded-l-md focus:outline-none focus:border-accent"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => setOrDropdownOpen(prev => !prev)}
                        className="px-2.5 py-2 bg-white border border-l-0 border-border rounded-r-md hover:bg-surface transition-colors"
                      >
                        <svg className={`w-3.5 h-3.5 text-text-secondary transition-transform ${orDropdownOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
                      </button>
                    </div>
                    {orDropdownOpen && (
                      <div className="absolute z-50 mt-1 w-full bg-white border border-border rounded-md shadow-lg max-h-[240px] overflow-y-auto">
                        {/* Auto option */}
                        <button
                          type="button"
                          onClick={() => {
                            setForm({ ...form, default_model: 'openrouter/auto' });
                            setOrDropdownOpen(false);
                            setOrModelSearch('');
                          }}
                          className={`w-full text-left px-3 py-2 text-[13px] hover:bg-accent/5 transition-colors ${form.default_model === 'openrouter/auto' ? 'bg-accent/10 text-accent font-medium' : 'text-text-primary'}`}
                        >
                          <div className="font-medium">openrouter/auto</div>
                          <div className="text-[11px] text-text-secondary">Auto-select best model</div>
                        </button>
                        <div className="border-t border-border" />
                        {orModelsLoading ? (
                          <div className="px-3 py-4 text-[12px] text-text-secondary text-center">Loading models...</div>
                        ) : (
                          orModels
                            .filter(m => {
                              if (!orModelSearch) return true;
                              const q = orModelSearch.toLowerCase();
                              return m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q);
                            })
                            .slice(0, 100)
                            .map(m => {
                              const price = parseFloat(m.pricing?.prompt ?? '0') * 1_000_000;
                              const isSelected = form.default_model === m.id;
                              return (
                                <button
                                  key={m.id}
                                  type="button"
                                  onClick={() => {
                                    setForm({ ...form, default_model: m.id });
                                    setOrDropdownOpen(false);
                                    setOrModelSearch('');
                                  }}
                                  className={`w-full text-left px-3 py-2 text-[13px] hover:bg-accent/5 transition-colors border-b border-border/50 last:border-0 ${isSelected ? 'bg-accent/10' : ''}`}
                                >
                                  <div className={`truncate ${isSelected ? 'text-accent font-medium' : 'text-text-primary'}`}>{m.id}</div>
                                  <div className="text-[11px] text-text-secondary flex gap-2">
                                    <span>{(m.context_length / 1000).toFixed(0)}k ctx</span>
                                    <span>{price === 0 ? 'Free' : `$${price.toFixed(2)}/M`}</span>
                                  </div>
                                </button>
                              );
                            })
                        )}
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={fetchOpenRouterModels}
                    disabled={orModelsLoading}
                    className="px-2 py-2 border border-border rounded-md hover:bg-surface transition-colors shrink-0"
                    title="Refresh model list"
                  >
                    <RefreshCw className={`w-4 h-4 text-text-secondary ${orModelsLoading ? 'animate-spin' : ''}`} />
                  </button>
                </div>
                {form.default_model && form.default_model !== 'openrouter/auto' && (() => {
                  const m = orModels.find(x => x.id === form.default_model);
                  if (!m) return null;
                  const price = parseFloat(m.pricing?.prompt ?? '0') * 1_000_000;
                  return (
                    <p className="text-[11px] text-text-secondary">
                      {(m.context_length / 1000).toFixed(0)}k context • {price === 0 ? 'Free' : `$${price.toFixed(2)}/M input tokens`}
                    </p>
                  );
                })()}
              </div>
            )}

            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">
                API Key{form.type === 'openrouter' ? ' (sk-or-...)' : ''} {editingId !== '' && '(leave blank to keep current)'}
              </label>
              <input type="password" value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} autoComplete="new-password" className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
            </div>
            <div className="space-y-1.5 flex items-end pb-2">
              <label className="flex items-center text-[13px] text-text-primary">
                <input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} className="mr-2" />
                Enabled
              </label>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={closeForm} className="px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary">Cancel</button>
            <button type="submit" disabled={saving} className="px-3 py-1.5 text-sm bg-text-primary text-white rounded-md disabled:opacity-50">
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      )}

      {providers.length === 0 ? (
        <div className="text-center py-8 bg-surface rounded-md border border-border border-dashed">
          <Database className="w-8 h-8 text-text-secondary mx-auto mb-2" />
          <p className="text-sm text-text-secondary">No LLM providers configured yet.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {providers.map((p) => (
            <div key={p.id} className="border border-border rounded-md p-4 flex items-center justify-between">
              <div>
                <div className="flex items-center">
                  <h4 className="text-sm font-medium text-text-primary mr-2">{p.name}</h4>
                  <span className="text-[10px] px-1.5 py-0.5 bg-surface border border-border rounded text-text-secondary uppercase">
                    {p.type}
                  </span>
                  {p.is_default && (
                    <span className="text-[10px] px-1.5 py-0.5 bg-accent/10 border border-accent/20 rounded text-accent ml-1 font-medium">
                      Default
                    </span>
                  )}
                  {p.enabled ? (
                    <CheckCircle className="w-4 h-4 text-success ml-2" />
                  ) : (
                    <XCircle className="w-4 h-4 text-danger ml-2" />
                  )}
                </div>
                <p className="text-xs text-text-secondary mt-1">
                  Default model: {p.default_model || 'None'} • {p.has_api_key ? 'Key configured' : 'No key'}
                </p>
              </div>
              <div className="flex items-center space-x-2">
                {!p.is_default && (
                  <button
                    onClick={async () => {
                      try {
                        await api.updateProvider(p.id, { is_default: true });
                        fetchProviders();
                        toast('Default provider set', 'success');
                      } catch (err: any) { toast(`Failed: ${err.message}`, 'error'); }
                    }}
                    className="text-xs font-medium text-accent hover:text-accent-hover px-2 py-1 rounded hover:bg-accent/5 transition-colors"
                  >
                    Set as Default
                  </button>
                )}
                <button
                  onClick={() => handleTest(p.id)}
                  className="text-xs font-medium text-accent hover:text-accent-hover px-2 py-1 rounded hover:bg-accent/5 transition-colors"
                >
                  Test Connection
                </button>
                <button onClick={() => openEdit(p)} className="p-1.5 text-text-secondary hover:text-text-primary hover:bg-surface rounded transition-colors">
                  <Edit2 className="w-4 h-4" />
                </button>
                <button onClick={() => handleDelete(p.id)} className="p-1.5 text-text-secondary hover:text-danger hover:bg-danger/5 rounded transition-colors">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CredentialsTab() {
  const toast = useToast();
  const confirm = useConfirm();
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  // null = form closed, '' = adding, otherwise the id of the credential being edited
  const [editingId, setEditingId] = useState<string | null>(null);
  
  // Form State
  const [name, setName] = useState('');
  const [type, setType] = useState<CredentialType>('api_key');
  const [sshAuthMethod, setSshAuthMethod] = useState<SshAuthMethod>('key');
  const [username, setUsername] = useState('');
  const [secret, setSecret] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [saving, setSaving] = useState(false);

  const fetchCredentials = async () => {
    try {
      setLoading(true);
      const data = await api.listCredentials();
      setCredentials(data);
    } catch (err) {
      console.error('Failed to fetch credentials', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCredentials();
  }, []);

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: 'Delete credential?',
      message: 'Anything using this credential will lose access to it.',
    });
    if (!ok) return;
    try {
      await api.deleteCredential(id);
      toast('Credential deleted', 'success');
      fetchCredentials();
    } catch (err: any) {
      toast(`Failed to delete credential: ${err.message}`, 'error');
    }
  };

  const openAdd = () => {
    setName('');
    setType('api_key');
    setSshAuthMethod('key');
    setUsername('');
    setSecret('');
    setPassphrase('');
    setEditingId('');
  };

  const openEdit = (c: Credential) => {
    setName(c.name);
    setType(c.type);
    setSshAuthMethod(c.ssh_auth_method || 'key');
    setUsername(c.username || '');
    setSecret('');
    setPassphrase('');
    setEditingId(c.id);
  };

  const closeForm = () => {
    setEditingId(null);
    setName('');
    setType('api_key');
    setSshAuthMethod('key');
    setUsername('');
    setSecret('');
    setPassphrase('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      if (editingId === '') {
        // Adding new
        await api.createCredential({
          name,
          type,
          ssh_auth_method: type === 'ssh_key' ? sshAuthMethod : undefined,
          username: type === 'ssh_key' ? username : (username || undefined),
          secret,
          passphrase: type === 'ssh_key' && sshAuthMethod === 'key' ? passphrase : undefined,
        });
      } else {
        // Editing existing — only send fields that changed
        const payload: Record<string, any> = {};
        if (name) payload.name = name;
        if (type === 'ssh_key') {
          payload.ssh_auth_method = sshAuthMethod;
          if (username) payload.username = username;
        } else if (username) {
          payload.username = username;
        }
        if (secret) payload.secret = secret;
        if (type === 'ssh_key' && sshAuthMethod === 'key' && passphrase) payload.passphrase = passphrase;
        await api.updateCredential(editingId!, payload);
      }
      toast(editingId === '' ? 'Credential saved' : 'Credential updated', 'success');
      closeForm();
      fetchCredentials();
    } catch (err: any) {
      toast(`Failed to ${editingId === '' ? 'add' : 'update'} credential: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-sm text-text-secondary">Loading credentials...</div>;

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-sm font-medium text-text-secondary">Secure Vault</h3>
        <button 
          onClick={() => (editingId === null ? openAdd() : closeForm())}
          className="bg-white border border-border hover:bg-surface text-text-primary px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center"
        >
          <Plus className="w-4 h-4 mr-1.5" />
          {editingId !== null ? 'Cancel' : 'Add Credential'}
        </button>
      </div>

      {editingId !== null && (
        <form onSubmit={handleSubmit} className="bg-surface border border-border rounded-md p-4 mb-6 space-y-4">
          <h4 className="text-sm font-medium text-text-primary">
            {editingId === '' ? 'New Credential' : `Edit Credential`}
          </h4>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">Name</label>
              <input required value={name} onChange={e => setName(e.target.value)} className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
            </div>
            {/* Type is read-only when editing */}
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">Type</label>
              {editingId === '' ? (
                <select value={type} onChange={e => setType(e.target.value as CredentialType)} className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md">
                  <option value="api_key">API Key</option>
                  <option value="ssh_key">SSH Key</option>
                  <option value="password">Password</option>
                  <option value="token">Token</option>
                </select>
              ) : (
                <div className="px-3 py-2 text-[13px] bg-surface border border-border rounded-md text-text-secondary capitalize">
                  {type.replace('_', ' ')}
                </div>
              )}
            </div>
            {/* SSH auth method selector */}
            {type === 'ssh_key' && (
              <div className="col-span-2">
                <label className="text-[12px] font-medium text-text-secondary uppercase mb-1.5 block">Authentication Method</label>
                <div className="flex gap-4">
                  <label className={`flex items-center gap-2 px-4 py-2 border rounded-md cursor-pointer text-[13px] transition-colors ${
                    sshAuthMethod === 'key'
                      ? 'border-accent bg-accent/5 text-text-primary'
                      : 'border-border bg-white text-text-secondary hover:bg-surface'
                  }`}>
                    <input
                      type="radio"
                      name="sshAuthMethod"
                      value="key"
                      checked={sshAuthMethod === 'key'}
                      onChange={() => setSshAuthMethod('key')}
                      className="accent-accent"
                    />
                    SSH Key
                  </label>
                  <label className={`flex items-center gap-2 px-4 py-2 border rounded-md cursor-pointer text-[13px] transition-colors ${
                    sshAuthMethod === 'password'
                      ? 'border-accent bg-accent/5 text-text-primary'
                      : 'border-border bg-white text-text-secondary hover:bg-surface'
                  }`}>
                    <input
                      type="radio"
                      name="sshAuthMethod"
                      value="password"
                      checked={sshAuthMethod === 'password'}
                      onChange={() => setSshAuthMethod('password')}
                      className="accent-accent"
                    />
                    Username + Password
                  </label>
                </div>
              </div>
            )}
            {/* Username field — required for SSH (key or password), optional for other types */}
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">
                {type === 'ssh_key' ? 'Username' : 'Username (optional)'}
              </label>
              <input
                value={username}
                onChange={e => setUsername(e.target.value)}
                required={type === 'ssh_key'}
                placeholder={type === 'ssh_key' ? 'e.g. root, ubuntu' : ''}
                className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md"
              />
            </div>
            {/* Secret field */}
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">
                {type === 'ssh_key' && sshAuthMethod === 'key' ? 'Private Key' : 'Secret Value'}
                {editingId !== '' && ' (leave blank to keep current)'}
              </label>
              {type === 'ssh_key' && sshAuthMethod === 'key' ? (
                <textarea
                  required={editingId === ''}
                  value={secret}
                  onChange={e => setSecret(e.target.value)}
                  placeholder={editingId !== '' ? 'Paste new key to overwrite current...' : '-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----'}
                  rows={5}
                  className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md font-mono"
                />
              ) : (
                <input
                  type="password"
                  required={editingId === ''}
                  value={secret}
                  onChange={e => setSecret(e.target.value)}
                  placeholder={editingId !== '' ? 'Enter new value to overwrite...' : ''}
                  className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md"
                />
              )}
            </div>
            {/* Passphrase — only for SSH key auth */}
            {type === 'ssh_key' && sshAuthMethod === 'key' && (
              <div className="space-y-1.5 col-span-2">
                <label className="text-[12px] font-medium text-text-secondary uppercase">Passphrase (optional{editingId !== '' ? ', leave blank to keep current' : ''})</label>
                <input type="password" value={passphrase} onChange={e => setPassphrase(e.target.value)} placeholder={editingId !== '' ? 'Enter new passphrase to overwrite...' : ''} className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
              </div>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={closeForm} className="px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary">Cancel</button>
            <button type="submit" disabled={saving} className="px-3 py-1.5 text-sm bg-text-primary text-white rounded-md disabled:opacity-50">
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      )}

      {credentials.length === 0 ? (
        <div className="text-center py-8 bg-surface rounded-md border border-border border-dashed">
          <Key className="w-8 h-8 text-text-secondary mx-auto mb-2" />
          <p className="text-sm text-text-secondary">No credentials stored yet.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {credentials.map((c) => (
            <div key={c.id} className="border border-border rounded-md p-4 flex items-center justify-between">
              <div>
                <div className="flex items-center">
                  <h4 className="text-sm font-medium text-text-primary mr-2">{c.name}</h4>
                  <span className="text-[10px] px-1.5 py-0.5 bg-surface border border-border rounded text-text-secondary uppercase">
                    {c.type}
                  </span>
                  {c.type === 'ssh_key' && c.ssh_auth_method && (
                    <span className="text-[10px] px-1.5 py-0.5 bg-accent/10 border border-accent/20 rounded text-accent ml-1">
                      {c.ssh_auth_method === 'key' ? 'SSH Key' : 'User/Pass'}
                    </span>
                  )}
                </div>
                <p className="text-xs text-text-secondary mt-1">
                  {c.username ? `User: ${c.username}` : 'No username'} • {c.has_secret ? 'Secret stored (encrypted)' : ''}
                </p>
              </div>
              <div className="flex items-center space-x-2">
                <button onClick={() => openEdit(c)} className="p-1.5 text-text-secondary hover:text-text-primary hover:bg-surface rounded transition-colors">
                  <Edit2 className="w-4 h-4" />
                </button>
                <button onClick={() => handleDelete(c.id)} className="p-1.5 text-text-secondary hover:text-danger hover:bg-danger/5 rounded transition-colors">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AccountTab() {
  const toast = useToast();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (newPassword.length < 10) {
      setError('New password must be at least 10 characters.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('New passwords do not match.');
      return;
    }
    setSaving(true);
    try {
      await api.changePassword({ current_password: currentPassword, new_password: newPassword });
      toast('Password changed successfully.', 'success');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to change password.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3 className="text-[13px] font-medium text-text-primary mb-4">Change password</h3>
      <form onSubmit={handleSubmit} className="space-y-3 max-w-sm">
        <div>
          <label className="block text-[12px] font-medium text-text-secondary mb-1">Current password</label>
          <input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className="input w-full"
            autoComplete="current-password"
            required
          />
        </div>
        <div>
          <label className="block text-[12px] font-medium text-text-secondary mb-1">New password <span className="text-text-secondary/60">(min 10 chars)</span></label>
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="input w-full"
            autoComplete="new-password"
            required
            minLength={10}
          />
        </div>
        <div>
          <label className="block text-[12px] font-medium text-text-secondary mb-1">Confirm new password</label>
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="input w-full"
            autoComplete="new-password"
            required
          />
        </div>
        {error && <p className="text-[12px] text-error">{error}</p>}
        <button type="submit" disabled={saving} className="btn-primary">
          {saving ? 'Saving…' : 'Update password'}
        </button>
      </form>
    </div>
  );
}

function ChannelsTab() {
  const toast = useToast();
  const confirm = useConfirm();
  const [configs, setConfigs] = useState<ChannelConfig[]>([]);
  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState<string | null>(null);

  // Token-edit form state: platform being edited (null = closed)
  const [tokenPlatform, setTokenPlatform] = useState<ChannelPlatform | null>(null);
  const [tokenValue, setTokenValue] = useState('');
  const [saving, setSaving] = useState(false);

  // New allowlist entry
  const [acctPlatform, setAcctPlatform] = useState<ChannelPlatform>('telegram');
  const [acctUserId, setAcctUserId] = useState('');
  const [acctLabel, setAcctLabel] = useState('');

  const PLATFORMS: { value: ChannelPlatform; label: string; help: string }[] = [
    { value: 'telegram', label: 'Telegram', help: 'Get a token from @BotFather. No public URL needed.' },
    { value: 'discord', label: 'Discord', help: 'Enable “Message Content Intent” in the Developer Portal, or the bot sees empty messages.' },
  ];

  const fetchAll = async () => {
    try {
      const [cfgs, accs] = await Promise.all([api.listChannelConfigs(), api.listChannelAccounts()]);
      setConfigs(cfgs);
      setAccounts(accs);
    } catch (err: any) {
      toast(`Failed to load channels: ${err.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAll();
  }, []);

  const cfgFor = (p: ChannelPlatform) => configs.find(c => c.platform === p);

  const saveToken = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenPlatform) return;
    if (!tokenValue.trim()) {
      toast('Enter a bot token.', 'error');
      return;
    }
    setSaving(true);
    try {
      const existing = cfgFor(tokenPlatform);
      await api.upsertChannelConfig(tokenPlatform, {
        bot_token: tokenValue.trim(),
        enabled: existing?.enabled ?? true,
        respond_in_groups: existing?.respond_in_groups ?? false,
      });
      toast(`${tokenPlatform} token saved. Gateway reloaded.`, 'success');
      setTokenPlatform(null);
      setTokenValue('');
      fetchAll();
    } catch (err: any) {
      toast(`Failed to save token: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (p: ChannelPlatform, field: 'enabled' | 'respond_in_groups', value: boolean) => {
    try {
      const cfg = cfgFor(p);
      await api.upsertChannelConfig(p, {
        enabled: field === 'enabled' ? value : cfg?.enabled ?? true,
        respond_in_groups: field === 'respond_in_groups' ? value : cfg?.respond_in_groups ?? false,
      });
      fetchAll();
    } catch (err: any) {
      toast(`Failed to update: ${err.message}`, 'error');
    }
  };

  const handleTest = async (p: ChannelPlatform) => {
    setTesting(p);
    try {
      const res = await api.testChannel(p);
      if (res.ok) toast(`${p} connected${res.bot_username ? ` as @${res.bot_username}` : ''}.`, 'success');
      else toast(`${p} test failed: ${res.error}`, 'error');
      fetchAll();
    } catch (err: any) {
      toast(`Test failed: ${err.message}`, 'error');
    } finally {
      setTesting(null);
    }
  };

  const handleDisconnect = async (p: ChannelPlatform) => {
    const ok = await confirm({
      title: `Disconnect ${p}?`,
      message: 'The stored token will be removed and the bot will go offline.',
    });
    if (!ok) return;
    try {
      await api.deleteChannelConfig(p);
      toast(`${p} disconnected.`, 'success');
      fetchAll();
    } catch (err: any) {
      toast(`Failed: ${err.message}`, 'error');
    }
  };

  const addAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!acctUserId.trim()) {
      toast('Enter an external user id.', 'error');
      return;
    }
    try {
      await api.upsertChannelAccount({
        platform: acctPlatform,
        external_user_id: acctUserId.trim(),
        allowed: true,
        label: acctLabel.trim() || null,
      });
      toast('User allowed.', 'success');
      setAcctUserId('');
      setAcctLabel('');
      fetchAll();
    } catch (err: any) {
      toast(`Failed: ${err.message}`, 'error');
    }
  };

  const toggleAccount = async (a: ChannelAccount, allowed: boolean) => {
    try {
      await api.upsertChannelAccount({
        platform: a.platform,
        external_user_id: a.external_user_id,
        allowed,
        label: a.label,
      });
      fetchAll();
    } catch (err: any) {
      toast(`Failed: ${err.message}`, 'error');
    }
  };

  const deleteAccount = async (id: string) => {
    try {
      await api.deleteChannelAccount(id);
      toast('Account removed.', 'success');
      fetchAll();
    } catch (err: any) {
      toast(`Failed: ${err.message}`, 'error');
    }
  };

  if (loading) return <div className="text-sm text-text-secondary">Loading channels...</div>;

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium text-text-secondary">Connected Bots</h3>
        </div>
        <p className="text-xs text-text-secondary mb-4">
          Access is <strong>default-deny</strong> — only allowlisted users can talk to Vigilus. Tokens are encrypted at rest.
        </p>
        <div className="space-y-4">
          {PLATFORMS.map(({ value, label, help }) => {
            const cfg = cfgFor(value);
            const connected = cfg && cfg.has_token;
            return (
              <div key={value} className="border border-border rounded-md p-4">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center">
                    <h4 className="text-sm font-medium text-text-primary mr-2">{label}</h4>
                    {connected ? (
                      <span className="text-[10px] px-1.5 py-0.5 bg-success/10 border border-success/20 rounded text-success font-medium uppercase">connected</span>
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 bg-surface border border-border rounded text-text-secondary uppercase">not connected</span>
                    )}
                    {cfg?.bot_username && (
                      <span className="text-[11px] text-text-secondary ml-2">@{cfg.bot_username}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {connected && (
                      <button onClick={() => handleTest(value)} disabled={testing === value} className="text-xs font-medium text-accent hover:text-accent-hover px-2 py-1 rounded hover:bg-accent/5 transition-colors disabled:opacity-50">
                        {testing === value ? 'Testing…' : 'Test'}
                      </button>
                    )}
                    {connected && (
                      <button onClick={() => setTokenPlatform(tokenPlatform === value ? null : value)} className="text-xs font-medium text-accent hover:text-accent-hover px-2 py-1 rounded hover:bg-accent/5 transition-colors">
                        {tokenPlatform === value ? 'Cancel' : 'Change token'}
                      </button>
                    )}
                    {connected && (
                      <button onClick={() => handleDisconnect(value)} className="p-1.5 text-text-secondary hover:text-danger hover:bg-danger/5 rounded transition-colors">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>

                {value === 'discord' && (
                  <p className="text-[11px] text-warning mb-2">⚠️ Requires the “Message Content Intent” in the Discord Developer Portal.</p>
                )}

                {tokenPlatform === value && (
                  <form onSubmit={saveToken} className="bg-surface border border-border rounded p-3 mt-2 space-y-2">
                    <label className="text-[12px] font-medium text-text-secondary uppercase">Bot token</label>
                    <input type="password" value={tokenValue} onChange={e => setTokenValue(e.target.value)} placeholder={`Paste ${label} bot token`} className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md font-mono" />
                    <p className="text-[11px] text-text-secondary">{help}</p>
                    <div className="flex justify-end gap-2">
                      <button type="button" onClick={() => { setTokenPlatform(null); setTokenValue(''); }} className="px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary">Cancel</button>
                      <button type="submit" disabled={saving} className="px-3 py-1.5 text-sm bg-text-primary text-white rounded-md disabled:opacity-50">{saving ? 'Saving…' : 'Save & reload'}</button>
                    </div>
                  </form>
                )}

                {!connected && (
                  <button onClick={() => setTokenPlatform(value)} className="mt-1 bg-white border border-border hover:bg-surface text-text-primary px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center">
                    <Plus className="w-4 h-4 mr-1.5" /> Connect {label}
                  </button>
                )}

                {connected && (
                  <div className="flex items-center gap-6 mt-2">
                    <label className="flex items-center text-[13px] text-text-primary">
                      <input type="checkbox" checked={cfg!.enabled} onChange={e => toggle(value, 'enabled', e.target.checked)} className="mr-2" />
                      Enabled
                    </label>
                    <label className="flex items-center text-[13px] text-text-primary">
                      <input type="checkbox" checked={cfg!.respond_in_groups} onChange={e => toggle(value, 'respond_in_groups', e.target.checked)} className="mr-2" />
                      Respond in groups (without @mention)
                    </label>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-text-secondary mb-4">Allowlist</h3>
        <form onSubmit={addAccount} className="bg-surface border border-border rounded-md p-4 mb-4">
          <div className="grid grid-cols-3 gap-3">
            <select value={acctPlatform} onChange={e => setAcctPlatform(e.target.value as ChannelPlatform)} className="px-3 py-2 text-[13px] bg-white border border-border rounded-md">
              {PLATFORMS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
            <input value={acctUserId} onChange={e => setAcctUserId(e.target.value)} placeholder="External user id" className="px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
            <input value={acctLabel} onChange={e => setAcctLabel(e.target.value)} placeholder="Label (optional)" className="px-3 py-2 text-[13px] bg-white border border-border rounded-md" />
          </div>
          <div className="flex justify-end mt-3">
            <button type="submit" className="px-3 py-1.5 text-sm bg-text-primary text-white rounded-md">Allow user</button>
          </div>
        </form>
        {accounts.length === 0 ? (
          <div className="text-center py-6 bg-surface rounded-md border border-border border-dashed">
            <p className="text-sm text-text-secondary">No allowlisted users yet.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {accounts.map(a => (
              <div key={a.id} className="border border-border rounded-md p-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] px-1.5 py-0.5 bg-surface border border-border rounded text-text-secondary uppercase">{a.platform}</span>
                  <span className="text-sm text-text-primary font-mono">{a.external_user_id}</span>
                  {a.label && <span className="text-xs text-text-secondary">— {a.label}</span>}
                  {a.allowed ? (
                    <CheckCircle className="w-4 h-4 text-success" />
                  ) : (
                    <XCircle className="w-4 h-4 text-danger" />
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => toggleAccount(a, !a.allowed)} className="text-xs font-medium text-accent hover:text-accent-hover px-2 py-1 rounded hover:bg-accent/5 transition-colors">
                    {a.allowed ? 'Revoke' : 'Allow'}
                  </button>
                  <button onClick={() => deleteAccount(a.id)} className="p-1.5 text-text-secondary hover:text-danger hover:bg-danger/5 rounded transition-colors">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SearchTab() {
  const [config, setConfig] = useState<import('../../types').SearchConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [searchBackend, setSearchBackend] = useState<'searxng' | 'firecrawl'>('searxng');
  const [fetchBackend, setFetchBackend] = useState<'builtin' | 'firecrawl'>('builtin');
  const [searxngUrl, setSearxngUrl] = useState('');
  const [firecrawlKey, setFirecrawlKey] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [test, setTest] = useState<import('../../types').SearchTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  const load = async () => {
    try {
      const cfg = await api.getSearchConfig();
      setConfig(cfg);
      setSearchBackend(cfg.search_backend);
      setFetchBackend(cfg.fetch_backend);
      setSearxngUrl(cfg.searxng_url || '');
      setEnabled(cfg.enabled);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const cfg = await api.upsertSearchConfig({
        search_backend: searchBackend,
        fetch_backend: fetchBackend,
        searxng_url: searxngUrl || null,
        firecrawl_api_key: firecrawlKey || undefined,
        enabled,
      });
      setConfig(cfg);
      setFirecrawlKey('');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.testSearch());
    } catch (e: any) {
      setTest({ ok: false, error: e?.message || 'Test failed' });
    } finally {
      setTesting(false);
    }
  };

  if (loading) return <div className="text-sm text-text-secondary">Loading…</div>;

  const usesFirecrawl = searchBackend === 'firecrawl' || fetchBackend === 'firecrawl';

  return (
    <div className="space-y-5">
      <p className="text-sm text-text-secondary">
        Web search and page fetch are performed by the Vigilus orchestrator only — operators
        never get web tools. Run SearXNG with no API key, or Firecrawl with a free-tier key.
      </p>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        <span className="text-text-primary">Enable web research</span>
      </label>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-text-secondary mb-1">Search backend</label>
          <select
            value={searchBackend}
            onChange={e => setSearchBackend(e.target.value as any)}
            className="w-full px-3 py-2 text-sm border border-border rounded-md bg-white"
          >
            <option value="searxng">SearXNG (self-hosted, no key)</option>
            <option value="firecrawl">Firecrawl (hosted, API key)</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-text-secondary mb-1">Fetch backend</label>
          <select
            value={fetchBackend}
            onChange={e => setFetchBackend(e.target.value as any)}
            className="w-full px-3 py-2 text-sm border border-border rounded-md bg-white"
          >
            <option value="builtin">Builtin (SSRF-safe, in-house)</option>
            <option value="firecrawl">Firecrawl scrape (hosted)</option>
          </select>
        </div>
      </div>

      {searchBackend === 'searxng' && (
        <div>
          <label className="block text-xs text-text-secondary mb-1">SearXNG URL</label>
          <input
            type="text"
            value={searxngUrl}
            onChange={e => setSearxngUrl(e.target.value)}
            placeholder="http://searxng.lan:8080"
            className="w-full px-3 py-2 text-sm border border-border rounded-md"
          />
          <p className="text-xs text-text-secondary mt-1">
            The instance must have the JSON format enabled in <code>settings.yml</code>
            (<code>search.formats: [html, json]</code>).
          </p>
        </div>
      )}

      {usesFirecrawl && (
        <div>
          <label className="block text-xs text-text-secondary mb-1">
            Firecrawl API key {config?.has_firecrawl_key && <span className="text-success">(configured)</span>}
          </label>
          <input
            type="password"
            value={firecrawlKey}
            onChange={e => setFirecrawlKey(e.target.value)}
            placeholder={config?.has_firecrawl_key ? '•••••••• (leave blank to keep)' : 'fc-…'}
            className="w-full px-3 py-2 text-sm border border-border rounded-md"
          />
        </div>
      )}

      <div className="flex items-center gap-3">
        <button onClick={handleSave} disabled={saving} className="px-3 py-1.5 text-sm bg-text-primary text-white rounded-md disabled:opacity-50">
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button onClick={handleTest} disabled={testing} className="px-3 py-1.5 text-sm border border-border rounded-md disabled:opacity-50">
          {testing ? 'Testing…' : 'Test'}
        </button>
      </div>

      {test && (
        <div className={`text-sm rounded-md p-3 ${test.ok ? 'bg-success/5 text-success' : 'bg-danger/5 text-danger'}`}>
          {test.ok
            ? `✓ ${test.backend} reachable (${test.result_count ?? 0} result(s)).`
            : `✗ ${test.error || 'Test failed.'}`}
          {test.hint && <pre className="mt-2 whitespace-pre-wrap text-xs text-text-secondary">{test.hint}</pre>}
        </div>
      )}
    </div>
  );
}

// Available IANA zones, with a safe fallback for runtimes lacking
// Intl.supportedValuesOf.
function listTimezones(): string[] {
  try {
    const fn = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] })
      .supportedValuesOf;
    if (fn) return fn('timeZone');
  } catch {
    // fall through
  }
  return ['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Tokyo'];
}

function GeneralTab() {
  const toast = useToast();
  const [timezone, setTimezone] = useState('UTC');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const zones = listTimezones();

  useEffect(() => {
    api
      .getOrchestratorConfig()
      .then((cfg) => setTimezone(cfg.timezone || 'UTC'))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async (tz: string) => {
    setTimezone(tz);
    setSaving(true);
    try {
      await api.updateOrchestratorConfig({ timezone: tz });
      toast('Timezone updated', 'success');
    } catch (err) {
      toast(`Failed to update timezone: ${(err as Error).message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-sm text-text-secondary">Loading…</div>;

  return (
    <div className="space-y-5 max-w-md">
      <div className="space-y-1.5">
        <label className="text-[12px] font-medium text-text-secondary uppercase">Timezone</label>
        <select
          value={timezone}
          disabled={saving}
          onChange={(e) => save(e.target.value)}
          className="input w-full"
        >
          {!zones.includes(timezone) && <option value={timezone}>{timezone}</option>}
          {zones.map((z) => (
            <option key={z} value={z}>{z}</option>
          ))}
        </select>
        <p className="text-[12px] text-text-secondary">
          Used to interpret scheduled task cron expressions and to display run times.
          Changing it reschedules all tasks.
        </p>
      </div>
    </div>
  );
}

export default function Settings() {
  const [activeTab, setActiveTab] = useState('providers');

  return (
    <div className="p-6 max-w-5xl">
      <div className="mb-8">
        <h1 className="text-2xl font-medium text-text-primary mb-1">Settings</h1>
        <p className="text-text-secondary text-sm">Manage platform configuration, credentials, and LLM providers.</p>
      </div>

      <div className="flex gap-8">
        {/* Settings Sidebar */}
        <div className="w-64 shrink-0">
          <nav className="space-y-1">
            <button
              onClick={() => setActiveTab('providers')}
              className={`w-full flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
                activeTab === 'providers' 
                  ? 'bg-accent/10 text-accent font-medium' 
                  : 'text-text-secondary hover:bg-surface hover:text-text-primary'
              }`}
            >
              <Database className="w-4 h-4 mr-3" />
              LLM Providers
            </button>
            <button
              onClick={() => setActiveTab('credentials')}
              className={`w-full flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
                activeTab === 'credentials' 
                  ? 'bg-accent/10 text-accent font-medium' 
                  : 'text-text-secondary hover:bg-surface hover:text-text-primary'
              }`}
            >
              <Key className="w-4 h-4 mr-3" />
              Credentials
            </button>
            <button
              onClick={() => setActiveTab('general')}
              className={`w-full flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
                activeTab === 'general'
                  ? 'bg-accent/10 text-accent font-medium'
                  : 'text-text-secondary hover:bg-surface hover:text-text-primary'
              }`}
            >
              <Sliders className="w-4 h-4 mr-3" />
              General
            </button>
            <button
              onClick={() => setActiveTab('channels')}
              className={`w-full flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
                activeTab === 'channels'
                  ? 'bg-accent/10 text-accent font-medium'
                  : 'text-text-secondary hover:bg-surface hover:text-text-primary'
              }`}
            >
              <Radio className="w-4 h-4 mr-3" />
              Channels
            </button>
            <button
              onClick={() => setActiveTab('search')}
              className={`w-full flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
                activeTab === 'search'
                  ? 'bg-accent/10 text-accent font-medium'
                  : 'text-text-secondary hover:bg-surface hover:text-text-primary'
              }`}
            >
              <Search className="w-4 h-4 mr-3" />
              Search
            </button>
            <button
              onClick={() => setActiveTab('account')}
              className={`w-full flex items-center px-3 py-2 text-sm rounded-md transition-colors ${
                activeTab === 'account'
                  ? 'bg-accent/10 text-accent font-medium'
                  : 'text-text-secondary hover:bg-surface hover:text-text-primary'
              }`}
            >
              <UserCog className="w-4 h-4 mr-3" />
              Account
            </button>
          </nav>
        </div>

        {/* Settings Content */}
        <div className="flex-1">
          <div className="bg-white border border-border rounded-card shadow-sm p-6">
            <h2 className="text-lg font-medium text-text-primary mb-4 border-b border-border pb-4">
              {activeTab === 'providers' && 'LLM Providers'}
              {activeTab === 'credentials' && 'Credentials'}
              {activeTab === 'general' && 'General Settings'}
              {activeTab === 'channels' && 'Channels'}
              {activeTab === 'search' && 'Web Search / Research'}
              {activeTab === 'account' && 'Account'}
            </h2>

            {activeTab === 'providers' && <ProvidersTab />}
            {activeTab === 'credentials' && <CredentialsTab />}
            {activeTab === 'general' && <GeneralTab />}
            {activeTab === 'channels' && <ChannelsTab />}
            {activeTab === 'search' && <SearchTab />}
            {activeTab === 'account' && <AccountTab />}
          </div>
        </div>
      </div>
    </div>
  );
}
