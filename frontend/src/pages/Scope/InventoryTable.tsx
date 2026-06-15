import { useMemo, useState } from 'react';
import { Trash2, Search, ShieldCheck, ArrowUpDown, Wifi, AlertTriangle } from 'lucide-react';
import type { ScopeInventoryHost } from '@/types';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useToast, useConfirm } from '@/components/Notifications';

type SortKey = 'ip' | 'hostname' | 'os' | 'open_port_count' | 'finding_count' | 'last_seen';

function relTime(iso: string | null): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diff = Date.now() - then;
  const m = Math.round(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

// IPs sort numerically by octet, not lexically, so 10.0.0.2 < 10.0.0.10.
function ipKey(ip: string): number {
  const parts = ip.split('.');
  if (parts.length !== 4) return Number.MAX_SAFE_INTEGER; // IPv6/other → end
  return parts.reduce((acc, p) => acc * 256 + (parseInt(p, 10) || 0), 0);
}

export function InventoryTable({
  hosts,
  loading,
  onSelect,
  onDeleted,
}: {
  hosts: ScopeInventoryHost[];
  loading: boolean;
  onSelect: (ip: string) => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('last_seen');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [deleting, setDeleting] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = q
      ? hosts.filter(
          (h) =>
            h.ip.toLowerCase().includes(q) ||
            (h.hostname || '').toLowerCase().includes(q) ||
            (h.os || '').toLowerCase().includes(q) ||
            (h.mac || '').toLowerCase().includes(q) ||
            h.services.some((s) => s.toLowerCase().includes(q)),
        )
      : hosts;

    const dir = sortDir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case 'ip':
          cmp = ipKey(a.ip) - ipKey(b.ip);
          break;
        case 'hostname':
          cmp = (a.hostname || '').localeCompare(b.hostname || '');
          break;
        case 'os':
          cmp = (a.os || '').localeCompare(b.os || '');
          break;
        case 'open_port_count':
          cmp = a.open_port_count - b.open_port_count;
          break;
        case 'finding_count':
          cmp = a.finding_count - b.finding_count;
          break;
        case 'last_seen':
          cmp =
            new Date(a.last_seen || 0).getTime() - new Date(b.last_seen || 0).getTime();
          break;
      }
      return cmp * dir;
    });
  }, [hosts, query, sortKey, sortDir]);

  const allVisibleSelected =
    filtered.length > 0 && filtered.every((h) => selected.has(h.ip));

  function toggle(ip: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(ip) ? next.delete(ip) : next.add(ip);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => {
      if (allVisibleSelected) {
        const next = new Set(prev);
        filtered.forEach((h) => next.delete(h.ip));
        return next;
      }
      const next = new Set(prev);
      filtered.forEach((h) => next.add(h.ip));
      return next;
    });
  }

  function setSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'ip' || key === 'hostname' || key === 'os' ? 'asc' : 'desc');
    }
  }

  async function handleDelete() {
    const ips = [...selected];
    if (ips.length === 0) return;
    const ok = await confirm({
      title: 'Delete from inventory',
      message:
        `Remove ${ips.length} host${ips.length > 1 ? 's' : ''} from your Scope inventory? ` +
        'This deletes the discovered host record, its open-port data, and scan-derived ' +
        'findings for these IPs. Managed servers are not affected.',
      confirmLabel: `Delete ${ips.length}`,
      danger: true,
    });
    if (!ok) return;
    setDeleting(true);
    try {
      const res = await api.scopeDeleteHosts(ips);
      toast(
        `Deleted ${res.deleted_hosts} host${res.deleted_hosts === 1 ? '' : 's'}` +
          (res.deleted_findings ? ` and ${res.deleted_findings} finding(s)` : ''),
        'success',
      );
      setSelected(new Set());
      onDeleted();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error');
    } finally {
      setDeleting(false);
    }
  }

  const SortHeader = ({ label, k, className }: { label: string; k: SortKey; className?: string }) => (
    <th className={cn('px-3 py-2 font-medium select-none', className)}>
      <button
        onClick={() => setSort(k)}
        className="inline-flex items-center gap-1 hover:text-text-primary"
      >
        {label}
        <ArrowUpDown className={cn('w-3 h-3', sortKey === k ? 'text-accent' : 'text-text-secondary/40')} />
      </button>
    </th>
  );

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="px-4 py-2.5 border-b border-border flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-text-secondary" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by IP, host, OS, service…"
            className="w-full pl-8 pr-2 py-1.5 text-[13px] bg-bg border border-border rounded-md text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent"
          />
        </div>
        <span className="text-[11px] text-text-secondary">
          {selected.size > 0 ? `${selected.size} selected · ` : ''}
          {filtered.length} host{filtered.length === 1 ? '' : 's'}
        </span>
        <button
          onClick={handleDelete}
          disabled={selected.size === 0 || deleting}
          className={cn(
            'ml-auto inline-flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-md font-medium transition-colors',
            selected.size === 0 || deleting
              ? 'bg-surface text-text-secondary/50 cursor-not-allowed'
              : 'bg-danger/10 text-danger hover:bg-danger/20',
          )}
        >
          <Trash2 className="w-3.5 h-3.5" />
          {deleting ? 'Deleting…' : `Delete${selected.size ? ` (${selected.size})` : ''}`}
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 min-h-0 overflow-auto">
        {loading ? (
          <div className="h-full flex items-center justify-center text-text-secondary text-sm">
            Loading inventory…
          </div>
        ) : filtered.length === 0 ? (
          <div className="h-full flex items-center justify-center text-text-secondary text-sm">
            {hosts.length === 0 ? 'No discovered hosts yet. Run a scan to populate Scope.' : 'No hosts match your filter.'}
          </div>
        ) : (
          <table className="w-full text-[13px] border-collapse">
            <thead className="sticky top-0 bg-surface text-text-secondary text-[11px] uppercase tracking-wide border-b border-border z-10">
              <tr>
                <th className="w-9 px-3 py-2">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleAll}
                    className="accent-accent cursor-pointer"
                    aria-label="Select all"
                  />
                </th>
                <SortHeader label="IP" k="ip" className="text-left" />
                <SortHeader label="Hostname" k="hostname" className="text-left" />
                <SortHeader label="OS" k="os" className="text-left" />
                <th className="px-3 py-2 font-medium text-left">MAC</th>
                <SortHeader label="Ports" k="open_port_count" className="text-left" />
                <SortHeader label="Findings" k="finding_count" className="text-left" />
                <SortHeader label="Last seen" k="last_seen" className="text-left" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((h) => {
                const isSel = selected.has(h.ip);
                return (
                  <tr
                    key={h.discovered_host_id}
                    className={cn(
                      'border-b border-border/60 hover:bg-surface/60 transition-colors',
                      isSel && 'bg-accent/5',
                    )}
                  >
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={isSel}
                        onChange={() => toggle(h.ip)}
                        className="accent-accent cursor-pointer"
                        aria-label={`Select ${h.ip}`}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => onSelect(h.ip)}
                        className="font-mono text-text-primary hover:text-accent inline-flex items-center gap-1.5"
                      >
                        {h.ip}
                        {h.managed && (
                          <ShieldCheck className="w-3.5 h-3.5 text-accent" aria-label="Managed" />
                        )}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-text-secondary truncate max-w-[160px]">
                      {h.hostname || '—'}
                    </td>
                    <td className="px-3 py-2 text-text-secondary truncate max-w-[160px]">
                      {h.os || '—'}
                    </td>
                    <td className="px-3 py-2 text-text-secondary font-mono text-[12px]">
                      {h.mac || '—'}
                    </td>
                    <td className="px-3 py-2">
                      {h.open_port_count > 0 ? (
                        <span
                          className="inline-flex items-center gap-1 text-text-secondary"
                          title={h.services.join(', ')}
                        >
                          <Wifi className="w-3.5 h-3.5" />
                          {h.open_port_count}
                        </span>
                      ) : (
                        <span className="text-text-secondary/50">0</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {h.finding_count > 0 ? (
                        <span className="inline-flex items-center gap-1 text-warning">
                          <AlertTriangle className="w-3.5 h-3.5" />
                          {h.finding_count}
                        </span>
                      ) : (
                        <span className="text-text-secondary/50">0</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-text-secondary whitespace-nowrap">
                      {relTime(h.last_seen)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
