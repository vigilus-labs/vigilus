import { Activity, X, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { cn } from '@/lib/utils';
import { useVigilusEvents } from '@/lib/ws';

interface ActivityDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function ActivityDrawer({ open, onClose }: ActivityDrawerProps) {
  const [actions, setActions] = useState<any[]>([]);

  useVigilusEvents({
    events: {
      'action.created': (e: any) => {
        setActions((prev) => [e.payload, ...prev].slice(0, 50));
      },
      'action.updated': (e: any) => {
        setActions((prev) => prev.map(a => a.id === e.payload.id ? e.payload : a));
      },
      'jit.requested': (e: any) => {
        setActions((prev) => [{...e.payload, isJit: true}, ...prev].slice(0, 50));
      }
    },
    enabled: open
  });



  return (
    <div
      className={cn(
        'border-l border-border bg-white dark:bg-bg transition-[width,opacity] duration-200 ease-in-out overflow-hidden shrink-0',
        open ? 'w-[320px] opacity-100' : 'w-0 opacity-0',
      )}
    >
      <div className="w-[320px] h-full flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between h-12 px-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <Activity className="w-3.5 h-3.5 text-accent" strokeWidth={1.75} />
            <span className="text-[13px] font-medium text-text-primary dark:text-text-primary">
              Live Activity
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:text-text-secondary dark:hover:text-text-primary dark:hover:bg-surface transition-colors"
          >
            <X className="w-3.5 h-3.5" strokeWidth={1.75} />
          </button>
        </div>

        {/* Actions feed */}
        <div className="flex-1 overflow-y-auto">
          {actions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full px-6 text-center">
              <div className="w-10 h-10 rounded-full bg-surface dark:bg-surface flex items-center justify-center mb-3">
                <ShieldCheck className="w-5 h-5 text-text-secondary/40 dark:text-text-secondary/40" strokeWidth={1.75} />
              </div>
              <p className="text-[13px] font-medium text-text-primary dark:text-text-primary mb-1">
                No recent activity
              </p>
              <p className="text-[12px] text-text-secondary dark:text-text-secondary leading-relaxed">
                Actions and approvals from your operators will appear here in real time.
              </p>
            </div>
          ) : (
            <div className="p-4 space-y-3">
              {actions.map((act, i) => (
                <div key={act.id || i} className="p-3 bg-surface/50 dark:bg-surface border border-border dark:border-border rounded-md text-[12px]">
                  <div className="flex justify-between items-start mb-1">
                    <span className="font-medium text-text-primary dark:text-text-primary">
                      {act.isJit ? 'JIT Request' : (act.tool_name || act.event)}
                    </span>
                    <span className={`px-1.5 py-0.5 rounded-sm text-[10px] uppercase font-medium ${
                      act.outcome === 'success' || act.status === 'approved' ? 'bg-success/10 text-success' : 
                      act.outcome === 'denied' || act.status === 'denied' ? 'bg-danger/10 text-danger' : 
                      'bg-warning/10 text-warning'
                    }`}>
                      {act.outcome || act.status || 'pending'}
                    </span>
                  </div>
                  <div className="text-text-secondary text-[11px] truncate">
                    {act.actor || act.task_description || act.operator_id}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-border px-4 py-2.5 shrink-0">
          <button className="text-[12px] text-accent hover:text-accent-hover transition-colors font-medium">
            View all actions →
          </button>
        </div>
      </div>
    </div>
  );
}
