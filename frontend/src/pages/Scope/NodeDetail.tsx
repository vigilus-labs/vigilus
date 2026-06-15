import { useEffect, useState } from 'react';
import { X, ShieldCheck, Radar, Wifi, AlertTriangle, Clock } from 'lucide-react';
import { api } from '@/lib/api';
import type { ScopeHostDetail } from '@/types';
import { cn } from '@/lib/utils';
import { severityColor } from './colors';

const SEV_ORDER = ['critical', 'high', 'medium', 'low', 'info'];

export function NodeDetail({ identity, onClose }: { identity: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ScopeHostDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .scopeHostDetail(identity)
      .then((d) => !cancelled && setDetail(d))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [identity]);

  return (
    <aside className="w-[360px] shrink-0 border-l border-border bg-white dark:bg-bg overflow-y-auto">
      <div className="flex items-center justify-between h-14 px-4 border-b border-border sticky top-0 bg-white dark:bg-bg z-10">
        <span className="text-sm font-medium text-text-primary truncate">
          {detail?.label ?? 'Host detail'}
        </span>
        <button onClick={onClose} className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-surface">
          <X className="w-4 h-4" />
        </button>
      </div>

      {loading ? (
        <div className="p-6 text-sm text-text-secondary">Loading…</div>
      ) : !detail ? (
        <div className="p-6 text-sm text-text-secondary">No data.</div>
      ) : (
        <div className="p-4 space-y-5">
          {/* Origins + identity */}
          <div className="flex flex-wrap items-center gap-1.5">
            {detail.origins.map((o) => (
              <span
                key={o}
                className={cn(
                  'inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded font-medium',
                  o === 'managed' && 'bg-accent/15 text-accent',
                  o === 'discovered' && 'bg-info/15 text-info',
                  o === 'monitored' && 'bg-info/15 text-info',
                )}
              >
                {o === 'managed' && <ShieldCheck className="w-3 h-3" />}
                {o === 'discovered' && <Radar className="w-3 h-3" />}
                {o === 'monitored' && <Wifi className="w-3 h-3" />}
                {o}
              </span>
            ))}
            {detail.monitored && !detail.origins.includes('monitored') && (
              <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-info/15 text-info">
                <Wifi className="w-3 h-3" /> wazuh
              </span>
            )}
          </div>

          <div className="space-y-1.5 text-[13px]">
            {detail.ip && (
              <div className="flex justify-between">
                <span className="text-text-secondary">IP</span>
                <span className="font-mono text-text-primary">{detail.ip}</span>
              </div>
            )}
            {detail.hostname && (
              <div className="flex justify-between">
                <span className="text-text-secondary">Hostname</span>
                <span className="font-mono text-text-primary">{detail.hostname}</span>
              </div>
            )}
            {detail.os && (
              <div className="flex justify-between">
                <span className="text-text-secondary">OS</span>
                <span className="text-text-primary">{detail.os}</span>
              </div>
            )}
          </div>

          {/* Ports */}
          <section>
            <h4 className="text-[11px] font-semibold uppercase text-text-secondary mb-2">
              Open Ports ({detail.ports.length})
            </h4>
            {detail.ports.length === 0 ? (
              <p className="text-xs text-text-secondary">None discovered.</p>
            ) : (
              <div className="space-y-1">
                {detail.ports.map((p) => (
                  <div
                    key={`${p.port}-${p.proto}`}
                    className="flex items-center justify-between bg-surface border border-border rounded px-2.5 py-1.5 text-[12px]"
                  >
                    <span className="font-mono text-text-primary">
                      {p.port}/{p.proto}
                    </span>
                    <span className="text-text-secondary">
                      {[p.service, p.product, p.version].filter(Boolean).join(' · ') || 'unknown'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Findings */}
          <section>
            <h4 className="text-[11px] font-semibold uppercase text-text-secondary mb-2">
              Findings ({detail.findings.length})
            </h4>
            {detail.findings.length === 0 ? (
              <p className="text-xs text-text-secondary">None recorded.</p>
            ) : (
              <div className="space-y-1.5">
                {[...detail.findings]
                  .sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity))
                  .map((f) => (
                    <div key={f.id} className="border border-border rounded px-2.5 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <AlertTriangle className="w-3 h-3 shrink-0" style={{ color: severityColor(f.severity) }} />
                        <span className="text-[12px] font-medium text-text-primary flex-1 truncate">{f.title}</span>
                      </div>
                      <div className="flex items-center gap-2 mt-1 text-[10px] text-text-secondary">
                        <span
                          className="px-1 py-0.5 rounded font-medium"
                          style={{ color: severityColor(f.severity), background: severityColor(f.severity).replace('rgb(', 'rgba(').replace(')', ', 0.12)') }}
                        >
                          {f.severity}
                        </span>
                        <span>{f.source}</span>
                        {f.count > 1 && <span>×{f.count}</span>}
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </section>

          {/* Recent actions */}
          <section>
            <h4 className="text-[11px] font-semibold uppercase text-text-secondary mb-2">
              Recent Activity
            </h4>
            {detail.recent_actions.length === 0 ? (
              <p className="text-xs text-text-secondary">No operator activity on this host.</p>
            ) : (
              <div className="space-y-1">
                {detail.recent_actions.map((a) => (
                  <div key={a.id} className="flex items-center justify-between text-[12px] py-1">
                    <span className="text-text-primary">{a.tool_name ?? a.event}</span>
                    <div className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          'text-[10px] px-1 py-0.5 rounded',
                          a.outcome === 'success' && 'bg-success/15 text-success',
                          a.outcome === 'error' && 'bg-danger/15 text-danger',
                          a.outcome === 'denied' && 'bg-warning/15 text-warning',
                        )}
                      >
                        {a.outcome}
                      </span>
                      {a.created_at && (
                        <span className="text-text-secondary inline-flex items-center gap-0.5">
                          <Clock className="w-2.5 h-2.5" />
                          {new Date(a.created_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}
