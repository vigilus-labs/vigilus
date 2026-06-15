import { useState, useEffect } from 'react';
import { X, CheckCircle2, AlertCircle, Loader2, ArrowRight, ArrowLeft } from 'lucide-react';
import { api } from '@/lib/api';
import type { Provider, ProviderCatalogEntry, ProviderType } from '@/types';

interface Props {
  onClose: () => void;
  onComplete: (provider: Provider) => void;
}

type Step = 'pick' | 'creds' | 'test' | 'done';

const LOGOS: Record<string, string> = {
  anthropic: 'A',
  openai: '⊕',
  openrouter: '↗',
  google: 'G',
  ollama: '🦙',
  custom: '⚙',
};

export function ProviderWizard({ onClose, onComplete }: Props) {
  const [step, setStep] = useState<Step>('pick');
  const [catalog, setCatalog] = useState<ProviderCatalogEntry[]>([]);
  const [selected, setSelected] = useState<ProviderCatalogEntry | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; models?: string[]; error?: string } | null>(null);
  const [selectedModel, setSelectedModel] = useState('');
  const [setAsDefault, setSetAsDefault] = useState(true);
  const [createdProvider, setCreatedProvider] = useState<Provider | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.getProviderCatalog().then(r => setCatalog(r.catalog)).catch(() => {});
  }, []);

  const pickProvider = (entry: ProviderCatalogEntry) => {
    setSelected(entry);
    setDisplayName(entry.label);
    setBaseUrl(entry.base_url ?? '');
    setApiKey('');
    setTestResult(null);
    setSelectedModel('');
    setError('');
    setStep('creds');
  };

  const goToTest = async () => {
    if (!selected) return;
    setError('');
    setSaving(true);
    try {
      const created = await api.createProvider({
        name: displayName || selected.label,
        type: selected.type as ProviderType,
        api_key: apiKey || null,
        base_url: baseUrl || null,
        default_model: selected.default_model || null,
        enabled: true,
      });
      setCreatedProvider(created);
      setStep('test');
      setTesting(true);
      const result = await api.testProvider(created.id);
      setTestResult(result);
      if (result.ok && result.models?.length) {
        setSelectedModel(result.models[0]);
      } else {
        setSelectedModel(selected.default_model ?? '');
      }
    } catch (err: any) {
      setError(err.message || 'Failed to create provider.');
    } finally {
      setSaving(false);
      setTesting(false);
    }
  };

  const finish = async () => {
    if (!createdProvider) return;
    setSaving(true);
    try {
      if (selectedModel && selectedModel !== createdProvider.default_model) {
        await api.updateProvider(createdProvider.id, { default_model: selectedModel });
      }
      if (setAsDefault) {
        await api.updateOrchestratorConfig({
          provider_id: createdProvider.id,
          model: selectedModel || null,
        });
      }
      onComplete(createdProvider);
      setStep('done');
    } catch (err: any) {
      setError(err.message || 'Failed to save configuration.');
    } finally {
      setSaving(false);
    }
  };

  const back = () => {
    if (step === 'creds') {
      setStep('pick');
      setSelected(null);
    } else if (step === 'test') {
      if (createdProvider) api.deleteProvider(createdProvider.id).catch(() => {});
      setCreatedProvider(null);
      setTestResult(null);
      setStep('creds');
    }
  };

  const STEPS: Step[] = ['pick', 'creds', 'test'];
  const STEP_LABELS: Record<Step, string> = { pick: 'Provider', creds: 'Credentials', test: 'Test', done: '' };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="relative w-[560px] max-h-[90vh] overflow-y-auto rounded-2xl bg-white dark:bg-surface border border-border dark:border-border shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border dark:border-border">
          <div>
            <div className="text-[15px] font-semibold text-text-primary dark:text-text-primary">
              {step === 'done' ? 'Provider added!' : 'Add AI Provider'}
            </div>
            <div className="text-[12px] text-text-secondary mt-0.5">
              {step === 'pick' && 'Choose a provider to get started'}
              {step === 'creds' && `Set up ${selected?.label}`}
              {step === 'test' && 'Testing connection'}
              {step === 'done' && 'Your provider is ready to use'}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-[#222] transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Step indicator */}
        {step !== 'done' && (
          <div className="flex items-center gap-2 px-6 py-3 border-b border-border dark:border-border">
            {STEPS.map((s, i) => (
              <div key={s} className="flex items-center gap-2">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold transition-colors ${
                  s === step
                    ? 'bg-accent text-white'
                    : STEPS.indexOf(s) < STEPS.indexOf(step)
                    ? 'bg-accent/20 text-accent'
                    : 'bg-surface dark:bg-[#222] text-text-secondary'
                }`}>
                  {i + 1}
                </div>
                <span className={`text-[12px] ${s === step ? 'text-text-primary dark:text-text-primary font-medium' : 'text-text-secondary'}`}>
                  {STEP_LABELS[s]}
                </span>
                {i < STEPS.length - 1 && <span className="text-border dark:text-[#333] ml-1">›</span>}
              </div>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="p-6">
          {/* Step 1: Pick */}
          {step === 'pick' && (
            <div className="grid grid-cols-2 gap-3">
              {catalog.map(entry => (
                <button
                  key={entry.id}
                  onClick={() => pickProvider(entry)}
                  className="flex items-center gap-3 px-4 py-3 rounded-xl border border-border dark:border-border hover:border-accent/40 hover:bg-accent/5 transition-colors text-left"
                >
                  <div className="w-10 h-10 rounded-lg bg-surface dark:bg-[#222] flex items-center justify-center text-[18px] shrink-0">
                    {LOGOS[entry.id] ?? entry.label[0]}
                  </div>
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium text-text-primary dark:text-text-primary">{entry.label}</div>
                    <div className="text-[11px] text-text-secondary truncate">
                      {entry.default_model ?? 'Any model'}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Step 2: Credentials */}
          {step === 'creds' && selected && (
            <div className="space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-text-secondary mb-1">Display Name</label>
                <input
                  value={displayName}
                  onChange={e => setDisplayName(e.target.value)}
                  className="w-full px-3 py-2 text-[13px] rounded-lg border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary focus:border-accent focus:ring-1 focus:ring-accent outline-none"
                />
              </div>

              {selected.needs_api_key && (
                <div>
                  <label className="block text-[12px] font-medium text-text-secondary mb-1">API Key</label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={e => setApiKey(e.target.value)}
                    placeholder={`Paste your ${selected.label} API key`}
                    className="w-full px-3 py-2 text-[13px] rounded-lg border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary placeholder:text-text-secondary/40 focus:border-accent focus:ring-1 focus:ring-accent outline-none"
                    autoFocus
                  />
                  {selected.key_url && (
                    <div className="mt-1.5 text-[11px] text-text-secondary">
                      Get your key at{' '}
                      <a href={selected.key_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                        {selected.key_url}
                      </a>
                    </div>
                  )}
                </div>
              )}

              {(selected.needs_base_url || selected.base_url) && (
                <div>
                  <label className="block text-[12px] font-medium text-text-secondary mb-1">
                    Base URL {!selected.needs_base_url && <span className="opacity-60">(optional)</span>}
                  </label>
                  <input
                    value={baseUrl}
                    onChange={e => setBaseUrl(e.target.value)}
                    placeholder={selected.base_url ?? 'https://...'}
                    className="w-full px-3 py-2 text-[13px] rounded-lg border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary placeholder:text-text-secondary/40 focus:border-accent focus:ring-1 focus:ring-accent outline-none"
                  />
                </div>
              )}

              {error && (
                <div className="flex items-center gap-2 text-[12px] text-danger px-3 py-2 rounded-lg bg-danger/5 border border-danger/20">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  {error}
                </div>
              )}
            </div>
          )}

          {/* Step 3: Test */}
          {step === 'test' && (
            <div className="space-y-4">
              {testing ? (
                <div className="flex items-center gap-3 py-8 justify-center text-text-secondary">
                  <Loader2 className="w-5 h-5 animate-spin" />
                  <span className="text-[13px]">Testing connection…</span>
                </div>
              ) : testResult ? (
                <div className="space-y-4">
                  <div className={`flex items-center gap-2 px-4 py-3 rounded-lg text-[13px] ${
                    testResult.ok
                      ? 'bg-success/5 border border-success/20 text-success'
                      : 'bg-danger/5 border border-danger/20 text-danger'
                  }`}>
                    {testResult.ok
                      ? <CheckCircle2 className="w-4 h-4 shrink-0" />
                      : <AlertCircle className="w-4 h-4 shrink-0" />}
                    {testResult.ok ? 'Connection successful!' : (testResult.error || 'Connection failed.')}
                  </div>

                  {testResult.ok && testResult.models && testResult.models.length > 0 && (
                    <div>
                      <label className="block text-[12px] font-medium text-text-secondary mb-1">
                        Model <span className="font-normal opacity-60">({testResult.models.length} available)</span>
                      </label>
                      <select
                        value={selectedModel}
                        onChange={e => setSelectedModel(e.target.value)}
                        className="w-full px-3 py-2 text-[13px] rounded-lg border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary"
                      >
                        {testResult.models.map(m => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </div>
                  )}

                  <label className="flex items-center gap-2.5 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={setAsDefault}
                      onChange={e => setSetAsDefault(e.target.checked)}
                      className="w-4 h-4 rounded"
                    />
                    <span className="text-[13px] text-text-primary dark:text-text-primary">
                      Set as orchestrator default provider
                    </span>
                  </label>
                </div>
              ) : null}

              {error && (
                <div className="flex items-center gap-2 text-[12px] text-danger px-3 py-2 rounded-lg bg-danger/5 border border-danger/20">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  {error}
                </div>
              )}
            </div>
          )}

          {/* Step 4: Done */}
          {step === 'done' && createdProvider && (
            <div className="flex flex-col items-center py-8 text-center">
              <div className="w-16 h-16 rounded-full bg-success/10 flex items-center justify-center mb-4">
                <CheckCircle2 className="w-8 h-8 text-success" />
              </div>
              <div className="text-[15px] font-semibold text-text-primary dark:text-text-primary mb-2">
                {createdProvider.name} is ready!
              </div>
              <div className="text-[13px] text-text-secondary max-w-[360px]">
                {setAsDefault
                  ? 'Set as your orchestrator default. You can start chatting now.'
                  : 'Provider saved. Select it from the orchestrator config to use it.'}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {step !== 'done' && (
          <div className="flex items-center justify-between px-6 py-4 border-t border-border dark:border-border">
            <button
              onClick={step === 'pick' ? onClose : back}
              className="px-4 py-2 text-[13px] rounded-lg text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-[#222] transition-colors flex items-center gap-1.5"
            >
              {step !== 'pick' && <ArrowLeft className="w-3.5 h-3.5" />}
              {step === 'pick' ? 'Cancel' : 'Back'}
            </button>

            {step === 'creds' && (
              <button
                onClick={goToTest}
                disabled={saving || (selected?.needs_api_key === true && !apiKey)}
                className="px-4 py-2 text-[13px] font-medium rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-50 transition-colors flex items-center gap-1.5"
              >
                {saving
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <ArrowRight className="w-3.5 h-3.5" />}
                Next
              </button>
            )}

            {step === 'test' && !testing && testResult && (
              <button
                onClick={finish}
                disabled={saving}
                className="px-4 py-2 text-[13px] font-medium rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-50 transition-colors flex items-center gap-1.5"
              >
                {saving
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <CheckCircle2 className="w-3.5 h-3.5" />}
                {testResult.ok ? 'Add Provider' : 'Add Anyway'}
              </button>
            )}
          </div>
        )}

        {step === 'done' && (
          <div className="flex justify-center px-6 py-4 border-t border-border dark:border-border">
            <button
              onClick={onClose}
              className="px-6 py-2 text-[13px] font-medium rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors"
            >
              Start chatting
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
