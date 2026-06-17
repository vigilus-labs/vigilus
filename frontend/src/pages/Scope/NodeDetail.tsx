import { useEffect, useState } from 'react';
import {
  X,
  ShieldCheck,
  Radar,
  Wifi,
  AlertTriangle,
  Clock,
  Router,
  Globe,
  Network,
  RadioTower,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { ScopeHostDetail, ScopeHostNode } from '@/types';
import { cn } from '@/lib/utils';
import { severityColor } from './colors';

const SEV_ORDER = ['critical', 'high', 'medium', 'low', 'info'];

export function NodeDetail({
  identity,
  hostNode,
  onClose,
  onRoleChanged,
}: {
  identity: string;
  hostNode?: ScopeHostNode;
  onClose: () => void;
  onRoleChanged?: () => void;
}) {
  const [detail, setDetail] = useState<ScopeHostDetail | null>(null);
  const [loading, setLoading] = useState(true);

  // Network-role state. Seeded from the host node (the topology carries the
  // saved flags); reset whenever the target host changes.
  const [roles, setRoles] = useState({
    is_gateway: hostNode?.is_gateway ?? false,
    is_dns: hostNode?.is_dns ?? false,
    is_switch: hostNode?.is_switch ?? false,
    is_access_point: hostNode?.is_access_point ?? false,
  });
  const [roleLabel, setRoleLabel] = useState(hostNode?.role_label ?? '');
  const [roleSaving, setRoleSaving] = useState(false);

  useEffect(() => {
    setRoles({
      is_gateway: hostNode?.is_gateway ?? false,
      is_dns: hostNode?.is_dns ?? false,
      is_switch: hostNode?.is_switch ?? false,
      is_access_point: hostNode?.is_access_point ?? false,
    });
    setRoleLabel(hostNode?.role_label ?? '');
  }, [hostNode]);

  const persistRole = async (next: typeof roles, label: string) => {
    if (!detail?.ip) return;
    setRoleSaving(true);
    try {
      await api.scopeSetHostRole({
        ip: detail.ip,
        is_gateway: next.is_gateway,
        is_dns: next.is_dns,
        is_switch: next.is_switch,
        is_access_point: next.is_access_point,
        label: label.trim() || null,
      });
      onRoleChanged?.();
    } finally {
      setRoleSaving(false);
    }
  };

  const toggleRole = (key: keyof typeof roles) => {
    const next = { ...roles, [key]: !roles[key] };
    setRoles(next);
    persistRole(next, roleLabel);
  };

  // Heuristic gateway suggestion: a .1/.254 address that isn't tagged yet.
  // Never auto-saves — just nudges the user to confirm the toggle.
  const hasAnyRole =
    roles.is_gateway || roles.is_dns || roles.is_switch || roles.is_access_point || !!roleLabel;
  const lastOctet = detail?.ip?.split('.').pop();
  const suggestGateway =
    !hasAnyRole && (lastOctet === '1' || lastOctet === '254');

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

          {/* Network role (manual tagging, keyed by IP) */}
          {detail.ip && (
            <section>
              <h4 className="text-[11px] font-semibold uppercase text-text-secondary mb-2">
                Network Role
                {roleSaving && <span className="ml-1.5 normal-case text-text-secondary">saving…</span>}
              </h4>
              <div className="grid grid-cols-2 gap-1.5">
                <RoleToggle
                  icon={Router}
                  label="Gateway"
                  active={roles.is_gateway}
                  suggest={suggestGateway}
                  onClick={() => toggleRole('is_gateway')}
                />
                <RoleToggle
                  icon={Globe}
                  label="DNS"
                  active={roles.is_dns}
                  onClick={() => toggleRole('is_dns')}
                />
                <RoleToggle
                  icon={Network}
                  label="Switch"
                  active={roles.is_switch}
                  onClick={() => toggleRole('is_switch')}
                />
                <RoleToggle
                  icon={RadioTower}
                  label="Access Point"
                  active={roles.is_access_point}
                  onClick={() => toggleRole('is_access_point')}
                />
              </div>
              <input
                type="text"
                value={roleLabel}
                onChange={(e) => setRoleLabel(e.target.value)}
                onBlur={() => persistRole(roles, roleLabel)}
                placeholder="Display label (optional)"
                className="mt-2 w-full px-2.5 py-1.5 text-[12px] rounded-md border border-border bg-surface text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </section>
          )}

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

/** A single role toggle button. `suggest` shows a subtle hint (e.g. for a
 * .1/.254 gateway) without pre-filling the state. */
function RoleToggle({
  icon: Icon,
  label,
  active,
  suggest,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  active: boolean;
  suggest?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-1.5 rounded-md border text-[12px] font-medium transition-colors',
        active
          ? 'border-accent bg-accent/15 text-accent'
          : 'border-border bg-surface text-text-secondary hover:text-text-primary hover:bg-bg',
        suggest && !active && 'border-warning/60 text-warning',
      )}
      title={suggest && !active ? `${label} (looks like one — click to confirm)` : label}
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
    </button>
  );
}
