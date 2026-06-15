import { useState, useEffect, useCallback } from 'react';
import { Brain, Plus, Trash2, Globe, Lock } from 'lucide-react';
import { api } from '@/lib/api';
import { Memory } from '@/types';

interface MemoryPanelProps {
  /** Scopes to display, e.g. ['global', 'orchestrator'] or ['global', operatorId]. */
  scopes: string[];
  /** Label shown for the non-global scope, e.g. 'Vigilus' or the operator name. */
  privateScopeLabel: string;
  /** Scope new memories are saved to ('global' by default). */
  defaultScope?: string;
}

export function MemoryPanel({ scopes, privateScopeLabel, defaultScope = 'global' }: MemoryPanelProps) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [draftScope, setDraftScope] = useState(defaultScope);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setMemories(await api.listMemories(scopes.join(',')));
    } catch (err) {
      console.error('Failed to load memories', err);
    } finally {
      setLoading(false);
    }
  }, [scopes.join(',')]);

  useEffect(() => { refresh(); }, [refresh]);

  const addMemory = async () => {
    const content = draft.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      const mem = await api.createMemory({ scope: draftScope, content });
      setMemories(prev => [mem, ...prev.filter(m => m.id !== mem.id)]);
      setDraft('');
    } catch (err) {
      console.error('Failed to save memory', err);
    } finally {
      setSaving(false);
    }
  };

  const removeMemory = async (id: string) => {
    try {
      await api.deleteMemory(id);
      setMemories(prev => prev.filter(m => m.id !== id));
    } catch (err) {
      console.error('Failed to delete memory', err);
    }
  };

  return (
    <div className="space-y-3">
      {/* Add row */}
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addMemory(); } }}
          placeholder="Add a fact to remember, e.g. 'arcane hosts the wiki in Docker'"
          className="flex-1 px-3 py-2 text-[13px] bg-white dark:bg-surface border border-border dark:border-border rounded-md focus:border-accent text-text-primary dark:text-text-primary placeholder:text-text-secondary/40"
        />
        <select
          value={draftScope}
          onChange={e => setDraftScope(e.target.value)}
          className="px-2 py-2 text-[12px] bg-white dark:bg-surface border border-border dark:border-border rounded-md text-text-secondary"
          title="Who can see this memory"
        >
          <option value="global">Shared (all agents)</option>
          {scopes.filter(s => s !== 'global').map(s => (
            <option key={s} value={s}>{privateScopeLabel} only</option>
          ))}
        </select>
        <button
          type="button"
          onClick={addMemory}
          disabled={!draft.trim() || saving}
          className="px-3 py-2 text-[12px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover disabled:opacity-50 transition-colors flex items-center"
        >
          <Plus className="w-3.5 h-3.5 mr-1" /> Add
        </button>
      </div>

      {/* Memory list */}
      <div className="border border-border dark:border-border rounded-md max-h-[260px] overflow-y-auto divide-y divide-border dark:divide-border bg-surface/30 dark:bg-surface/30">
        {loading ? (
          <div className="py-8 text-center text-[12px] text-text-secondary">Loading memories…</div>
        ) : memories.length === 0 ? (
          <div className="py-8 px-4 text-center text-[12px] text-text-secondary">
            <Brain className="w-6 h-6 mx-auto mb-2 opacity-40" />
            No memories yet. Agents save facts here as they learn your environment —
            or add one yourself above.
          </div>
        ) : (
          memories.map(m => (
            <div key={m.id} className="group flex items-start gap-2.5 px-3 py-2.5">
              <div className="mt-0.5 shrink-0" title={m.scope === 'global' ? 'Shared with all agents' : `${privateScopeLabel} only`}>
                {m.scope === 'global'
                  ? <Globe className="w-3.5 h-3.5 text-text-secondary/60" />
                  : <Lock className="w-3.5 h-3.5 text-amber-500/80" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-text-primary dark:text-text-primary leading-snug">
                  {m.category && (
                    <span className="inline-block mr-1.5 px-1.5 py-px rounded text-[10px] uppercase tracking-wider bg-accent/10 text-accent align-middle">
                      {m.category}
                    </span>
                  )}
                  {m.content}
                </div>
                <div className="text-[11px] text-text-secondary/50 mt-0.5">
                  {m.source ? `by ${m.source} · ` : ''}{new Date(m.created_at).toLocaleDateString()}
                </div>
              </div>
              <button
                type="button"
                onClick={() => removeMemory(m.id)}
                className="shrink-0 p-1 rounded text-text-secondary/40 hover:text-danger hover:bg-danger/5 transition-all opacity-0 group-hover:opacity-100"
                title="Forget this memory"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
