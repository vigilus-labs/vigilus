import { useEffect, useMemo, useState } from 'react';
import { X, Search, Radar } from 'lucide-react';
import { api } from '@/lib/api';
import type { ScopeInventoryHost } from '@/types';

/** Modal for picking an unmanaged discovered host to seed the Add Server form.

 * Fetches the Scope inventory, filters to hosts with no matching Server, and
 * lets the user search/select one. Doesn't create anything itself — it just
 * hands the chosen host back so the caller can prefill the normal add form. */
export function PickFromScopeModal({
  onClose,
  onPick,
}: {
  onClose: () => void;
  onPick: (host: ScopeInventoryHost) => void;
}) {
  const [hosts, setHosts] = useState<ScopeInventoryHost[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    api
      .scopeInventory()
      .then((rows) => {
        if (!cancelled) setHosts(rows.filter((h) => !h.managed));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return hosts;
    return hosts.filter(
      (h) =>
        h.ip.toLowerCase().includes(q) ||
        (h.hostname || '').toLowerCase().includes(q) ||
        (h.os || '').toLowerCase().includes(q),
    );
  }, [hosts, query]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-[440px] max-h-[70vh] flex flex-col bg-white dark:bg-surface border border-border rounded-card shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between h-12 px-4 border-b border-border shrink-0">
          <span className="text-sm font-medium text-text-primary truncate">Add from Scope</span>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-3 border-b border-border shrink-0">
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-text-secondary" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by IP, hostname, OS…"
              className="w-full pl-8 pr-2 py-1.5 text-[13px] bg-bg border border-border rounded-md text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent"
            />
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto">
          {loading ? (
            <div className="p-6 text-sm text-text-secondary text-center">Loading discovered hosts…</div>
          ) : filtered.length === 0 ? (
            <div className="p-8 text-center text-text-secondary">
              <Radar className="w-8 h-8 mx-auto mb-2 opacity-40" />
              <p className="text-sm font-medium">
                {hosts.length === 0 ? 'No unmanaged hosts in Scope' : 'No hosts match your filter'}
              </p>
              <p className="text-xs mt-1">
                {hosts.length === 0 && 'Every discovered host is already a managed server, or no scans have run yet.'}
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-border/60">
              {filtered.map((h) => (
                <li key={h.discovered_host_id}>
                  <button
                    onClick={() => onPick(h)}
                    className="w-full text-left px-4 py-2.5 hover:bg-accent/5 transition-colors flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[13px] text-text-primary">{h.ip}</span>
                        {h.hostname && (
                          <span className="text-[12px] text-text-secondary truncate">{h.hostname}</span>
                        )}
                      </div>
                      <div className="text-[11px] text-text-secondary mt-0.5">
                        {[h.os, h.services.length ? `${h.services.length} open port(s)` : null]
                          .filter(Boolean)
                          .join(' · ') || 'No extra detail'}
                      </div>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
