import { useCallback, useEffect, useState } from 'react';
import { KeyRound, ChevronDown, ChevronUp } from 'lucide-react';
import { api } from '@/lib/api';
import { useVigilusEvents } from '@/lib/ws';
import { JitGrantControls, JitGrantOpts } from '@/components/JitGrantControls';

// Minimal shape the banner needs — works for both the REST list and the
// `jit.requested` WS payload.
interface PendingJit {
  id: string;
  operator_name?: string;
  resource?: string;
  permission?: string;
  task_description?: string;
  resolving?: boolean;
}

/**
 * App-wide JIT approval banner. Floats above all page content (including the
 * chat hero) so an elevation request raised anywhere — chat, channels, or an
 * unattended scheduled task — can be approved or denied without navigating to
 * the JIT page. Disappears when nothing is pending.
 */
export function JitBanner() {
  const [pending, setPending] = useState<PendingJit[]>([]);
  const [collapsed, setCollapsed] = useState(false);

  const loadPending = useCallback(async () => {
    try {
      const reqs = await api.listJitRequests({ status: 'pending' });
      setPending(
        reqs.map(r => ({
          id: r.id,
          operator_name: r.operator_name,
          resource: r.resource,
          permission: r.permission,
          task_description: r.task_description,
        })),
      );
    } catch {
      // best-effort; WS events keep us in sync once connected
    }
  }, []);

  useEffect(() => {
    loadPending();
  }, [loadPending]);

  useVigilusEvents({
    events: {
      'jit.requested': event => {
        const p = event.payload as PendingJit & { status?: string };
        if (!p?.id || p.status !== 'pending') return;
        setPending(prev => (prev.some(j => j.id === p.id) ? prev : [...prev, p]));
        setCollapsed(false);
      },
      'jit.resolved': event => {
        const p = event.payload as { id?: string };
        if (!p?.id) return;
        setPending(prev => prev.filter(j => j.id !== p.id));
      },
    },
  });

  const resolve = async (item: PendingJit, action: 'approve' | 'deny', opts?: JitGrantOpts) => {
    setPending(prev => prev.map(j => (j.id === item.id ? { ...j, resolving: true } : j)));
    try {
      if (action === 'approve') {
        await api.approveJitRequest(item.id, opts);
      } else {
        await api.denyJitRequest(item.id);
      }
      // Optimistically drop it; the jit.resolved event will also remove it.
      setPending(prev => prev.filter(j => j.id !== item.id));
    } catch {
      setPending(prev => prev.map(j => (j.id === item.id ? { ...j, resolving: false } : j)));
    }
  };

  if (pending.length === 0) return null;

  return (
    <div className="fixed top-3 left-1/2 -translate-x-1/2 z-[100] w-[min(560px,calc(100vw-2rem))] pointer-events-none">
      <div className="pointer-events-auto rounded-card border border-amber-500/40 bg-white dark:bg-surface shadow-xl shadow-black/10 overflow-hidden">
        {/* Header */}
        <button
          onClick={() => setCollapsed(c => !c)}
          className="w-full flex items-center gap-2 px-4 py-2.5 bg-amber-500/10 text-left"
        >
          <KeyRound className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
          <span className="text-[13px] font-medium text-text-primary dark:text-text-primary">
            {pending.length} JIT approval{pending.length === 1 ? '' : 's'} pending
          </span>
          <span className="ml-auto text-text-secondary">
            {collapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          </span>
        </button>

        {!collapsed && (
          <div className="max-h-[60vh] overflow-y-auto divide-y divide-border dark:divide-border">
            {pending.map(item => (
              <div key={item.id} className="px-4 py-3">
                <div className="text-[13px] text-text-primary dark:text-text-primary mb-1">
                  <span className="font-medium">{item.operator_name || 'An operator'}</span>{' '}
                  needs{' '}
                  <span className="font-mono font-medium">{item.permission || 'elevated'}</span>{' '}
                  access
                  {item.resource && item.resource !== '*' && (
                    <>
                      {' '}
                      to <span className="font-mono">{item.resource}</span>
                    </>
                  )}
                </div>
                {item.task_description && (
                  <div className="text-[12px] text-text-secondary mb-2">{item.task_description}</div>
                )}
                <JitGrantControls
                  resource={item.resource}
                  busy={item.resolving}
                  onApprove={opts => resolve(item, 'approve', opts)}
                  onDeny={() => resolve(item, 'deny')}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
