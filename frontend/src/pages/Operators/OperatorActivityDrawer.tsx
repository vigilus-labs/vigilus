import { useEffect, useRef, useState } from 'react';
import { X, Wrench, CornerDownRight, GitBranch, Brain, AlertTriangle, MessageSquare, Loader2, CircleDot, Square, ChevronDown, ChevronRight } from 'lucide-react';
import { api } from '@/lib/api';
import type { Operator, RunningTask, RunningTaskActivity } from '@/types';
import { describeActivity, type ActivityView } from '@/lib/operatorStatus';
import { cn } from '@/lib/utils';

const TONE_ICON: Record<ActivityView['tone'], React.ElementType> = {
  tool: Wrench,
  result: CornerDownRight,
  delegation: GitBranch,
  thinking: Brain,
  warning: AlertTriangle,
  error: AlertTriangle,
  text: MessageSquare,
};

const TONE_COLOR: Record<ActivityView['tone'], string> = {
  tool: 'text-accent',
  result: 'text-success',
  delegation: 'text-info',
  thinking: 'text-text-secondary',
  warning: 'text-warning',
  error: 'text-danger',
  text: 'text-text-secondary',
};

function fmtElapsed(seconds: number): string {
  const s = Math.floor(seconds % 60);
  const m = Math.floor(seconds / 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** Compact live token total, e.g. "≈ 12.4k tok · $0.03". */
function fmtTokens(tokensIn?: number, tokensOut?: number, costUsd?: number | null): string | null {
  const total = (tokensIn ?? 0) + (tokensOut ?? 0);
  if (!total) return null;
  const compact = total >= 1000 ? `${(total / 1000).toFixed(1)}k` : String(total);
  const cost = typeof costUsd === 'number' ? ` · $${costUsd.toFixed(2)}` : '';
  return `≈ ${compact} tok${cost}`;
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
  const [usage, setUsage] = useState<{ in: number; out: number; cost: number | null }>({ in: 0, out: 0, cost: null });
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [cancelling, setCancelling] = useState(false);
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
        setUsage({ in: d.tokens_in ?? 0, out: d.tokens_out ?? 0, cost: d.cost_usd ?? null });
        if (!d.running) setCancelling(false);
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

  const toggleExpand = (i: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  const onStop = async () => {
    if (!sessionId || cancelling) return;
    setCancelling(true);
    try {
      await api.cancelRunningTask(sessionId);
    } catch (err) {
      console.error('Failed to cancel task', err);
      setCancelling(false);
    }
  };

  const watched = tasks.find((t) => t.session_id === sessionId);
  const tokenLine = fmtTokens(usage.in, usage.out, usage.cost);

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
          <div className="flex items-center gap-1.5">
            {running && (
              <button
                onClick={onStop}
                disabled={cancelling}
                title="Stop this run — takes effect between tool calls and LLM requests"
                className={cn(
                  'flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[12px] font-medium transition-colors',
                  cancelling
                    ? 'bg-surface text-text-secondary cursor-wait'
                    : 'bg-danger/10 text-danger hover:bg-danger/20',
                )}
              >
                {cancelling ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Square className="w-3.5 h-3.5 fill-current" />
                )}
                {cancelling ? 'Cancelling…' : 'Stop'}
              </button>
            )}
            <button onClick={onClose} className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-surface">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Current step + session switcher */}
        <div className="px-4 py-3 border-b border-border shrink-0 space-y-2">
          <div className="text-[13px] text-text-primary">
            {step || (running ? 'Working…' : 'Not currently running.')}
          </div>
          <div className="text-[11px] text-text-secondary flex items-center gap-2 flex-wrap">
            {watched && (
              <>
                <span className="truncate max-w-[220px]">{watched.title}</span>
                <span>·</span>
                <span>{fmtElapsed(watched.elapsed_seconds)}</span>
              </>
            )}
            {tokenLine && (
              <>
                <span>·</span>
                <span className="tabular-nums">{tokenLine}</span>
              </>
            )}
          </div>
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
              // Tool calls carry the full (server-redacted) arguments — show a
              // preview collapsed, the complete object expanded.
              const hasArgs = ev.type === 'tool_call' && ev.data?.args && Object.keys(ev.data.args).length > 0;
              const isOpen = expanded.has(i);
              return (
                <div key={i} className="text-[12px]">
                  <div
                    className={cn('flex gap-2.5', hasArgs && 'cursor-pointer')}
                    onClick={hasArgs ? () => toggleExpand(i) : undefined}
                  >
                    <Icon className={cn('w-3.5 h-3.5 mt-0.5 shrink-0', TONE_COLOR[v.tone], v.tone === 'warning' && 'animate-pulse')} />
                    <div className="min-w-0 flex-1">
                      <div className="text-text-primary inline-flex items-center gap-1">
                        {hasArgs && (isOpen
                          ? <ChevronDown className="w-3 h-3 text-text-secondary shrink-0" />
                          : <ChevronRight className="w-3 h-3 text-text-secondary shrink-0" />)}
                        {v.label}
                      </div>
                      {v.detail && (
                        <div className="text-text-secondary truncate font-mono text-[11px] mt-0.5">{v.detail}</div>
                      )}
                    </div>
                    <span className="text-[10px] text-text-secondary/60 shrink-0 tabular-nums">
                      {new Date(ev.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                  </div>
                  {hasArgs && isOpen && (
                    <pre className="mt-1 ml-6 mr-8 p-2 rounded bg-surface border border-border overflow-x-auto text-[11px] font-mono text-text-primary whitespace-pre-wrap break-all">
                      {JSON.stringify(ev.data.args, null, 2)}
                    </pre>
                  )}
                </div>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}
