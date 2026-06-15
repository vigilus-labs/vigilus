import { useEffect, useRef, useState } from 'react';
import { X, Wrench, CornerDownRight, GitBranch, Brain, AlertTriangle, MessageSquare, Loader2, CircleDot } from 'lucide-react';
import { api } from '@/lib/api';
import type { Operator, RunningTask, RunningTaskActivity } from '@/types';
import { describeActivity, type ActivityView } from '@/lib/operatorStatus';
import { cn } from '@/lib/utils';

const TONE_ICON: Record<ActivityView['tone'], React.ElementType> = {
  tool: Wrench,
  result: CornerDownRight,
  delegation: GitBranch,
  thinking: Brain,
  error: AlertTriangle,
  text: MessageSquare,
};

const TONE_COLOR: Record<ActivityView['tone'], string> = {
  tool: 'text-accent',
  result: 'text-success',
  delegation: 'text-info',
  thinking: 'text-text-secondary',
  error: 'text-danger',
  text: 'text-text-secondary',
};

function fmtElapsed(seconds: number): string {
  const s = Math.floor(seconds % 60);
  const m = Math.floor(seconds / 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function OperatorActivityDrawer({
  operator,
  tasks,
  onClose,
}: {
  operator: Operator;
  tasks: RunningTask[];
  onClose: () => void;
}) {
  // Which running session we're watching (an operator can run in several).
  const [sessionId, setSessionId] = useState<string | null>(tasks[0]?.session_id ?? null);
  const [activity, setActivity] = useState<RunningTaskActivity[]>([]);
  const [running, setRunning] = useState<boolean>(tasks.length > 0);
  const [step, setStep] = useState<string | null>(tasks[0]?.current_step ?? null);
  const feedRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);

  // Keep the selected session valid as the task list changes.
  useEffect(() => {
    if (!sessionId || !tasks.some((t) => t.session_id === sessionId)) {
      setSessionId(tasks[0]?.session_id ?? sessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks]);

  // Poll the buffered activity for the watched session.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const d = await api.getRunningTask(sessionId);
        if (cancelled) return;
        setActivity(d.activity ?? []);
        setRunning(!!d.running);
        setStep(d.current_step ?? null);
      } catch {
        /* transient — keep last state */
      }
    };
    tick();
    const iv = setInterval(tick, 1500);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [sessionId]);

  // Auto-scroll to newest unless the user scrolled up.
  useEffect(() => {
    const el = feedRef.current;
    if (el && atBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [activity]);

  const onScroll = () => {
    const el = feedRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const watched = tasks.find((t) => t.session_id === sessionId);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative w-[460px] max-w-[90vw] h-full bg-white dark:bg-bg border-l border-border shadow-xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between h-14 px-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-medium text-text-primary truncate">{operator.name}</span>
            <span
              className={cn(
                'inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded font-medium',
                running ? 'bg-info/15 text-info' : 'bg-surface text-text-secondary',
              )}
            >
              {running ? <Loader2 className="w-3 h-3 animate-spin" /> : <CircleDot className="w-3 h-3" />}
              {running ? 'Running' : 'Idle'}
            </span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-surface">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Current step + session switcher */}
        <div className="px-4 py-3 border-b border-border shrink-0 space-y-2">
          <div className="text-[13px] text-text-primary">
            {step || (running ? 'Working…' : 'Not currently running.')}
          </div>
          {watched && (
            <div className="text-[11px] text-text-secondary flex items-center gap-2">
              <span className="truncate">{watched.title}</span>
              <span>·</span>
              <span>{fmtElapsed(watched.elapsed_seconds)}</span>
            </div>
          )}
          {tasks.length > 1 && (
            <select
              value={sessionId ?? ''}
              onChange={(e) => setSessionId(e.target.value)}
              className="w-full mt-1 px-2 py-1 text-[12px] bg-bg border border-border rounded-md text-text-primary"
            >
              {tasks.map((t) => (
                <option key={t.session_id} value={t.session_id}>
                  {t.title} ({fmtElapsed(t.elapsed_seconds)})
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Activity feed */}
        <div ref={feedRef} onScroll={onScroll} className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-2">
          {activity.length === 0 ? (
            <div className="h-full flex items-center justify-center text-[13px] text-text-secondary text-center">
              {running ? 'Waiting for activity…' : 'No activity recorded for this session.'}
            </div>
          ) : (
            activity.map((ev, i) => {
              const v = describeActivity(ev);
              const Icon = TONE_ICON[v.tone];
              return (
                <div key={i} className="flex gap-2.5 text-[12px]">
                  <Icon className={cn('w-3.5 h-3.5 mt-0.5 shrink-0', TONE_COLOR[v.tone])} />
                  <div className="min-w-0 flex-1">
                    <div className="text-text-primary">{v.label}</div>
                    {v.detail && (
                      <div className="text-text-secondary truncate font-mono text-[11px] mt-0.5">{v.detail}</div>
                    )}
                  </div>
                  <span className="text-[10px] text-text-secondary/60 shrink-0 tabular-nums">
                    {new Date(ev.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}
