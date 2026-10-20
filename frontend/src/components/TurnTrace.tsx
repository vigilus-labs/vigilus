import { useState } from 'react';
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Clock, ShieldX, Wrench } from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { ActionOutcome, TraceStep, TurnTrace as TurnTraceData } from '@/types';

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

// "command=systemctl restart nginx" — the first couple of args, for a one-line
// row. server_id is left out because the row already says which server.
function summarizeArgs(args: Record<string, unknown> | null): string {
  if (!args) return '';
  return Object.entries(args)
    .filter(([k]) => k !== 'server_id')
    .slice(0, 2)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(' ');
}

function OutcomeIcon({ outcome }: { outcome: ActionOutcome }) {
  switch (outcome) {
    case 'success': return <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-success" />;
    case 'error': return <AlertCircle className="w-3.5 h-3.5 shrink-0 text-danger" />;
    case 'denied': return <ShieldX className="w-3.5 h-3.5 shrink-0 text-warning" />;
    default: return <Clock className="w-3.5 h-3.5 shrink-0 text-text-secondary" />;
  }
}

type OutputState = { loading: boolean; text?: string | null; failed?: boolean };

function StepRow({ step }: { step: TraceStep }) {
  const [open, setOpen] = useState(false);
  const [output, setOutput] = useState<OutputState | null>(null);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !output) {
      setOutput({ loading: true });
      api.getAction(step.action_id)
        .then(a => setOutput({ loading: false, text: a.output }))
        .catch(() => setOutput({ loading: false, failed: true }));
    }
  };

  const args = summarizeArgs(step.args);

  return (
    <div className="rounded-md hover:bg-surface/60 dark:hover:bg-border/30">
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center gap-2 px-2 py-1.5 text-left text-[12px]"
      >
        {open
          ? <ChevronDown className="w-3 h-3 shrink-0 text-text-secondary/60" />
          : <ChevronRight className="w-3 h-3 shrink-0 text-text-secondary/60" />}
        <OutcomeIcon outcome={step.outcome} />
        <span className="shrink-0 text-text-secondary">{step.actor}</span>
        <span className="shrink-0 font-mono font-medium text-text-primary">{step.tool_name || 'tool'}</span>
        {step.server_name && (
          <span className="shrink-0 text-text-secondary">
            on <span className="font-mono text-text-primary">{step.server_name}</span>
          </span>
        )}
        <span className="min-w-0 flex-1 truncate font-mono text-text-secondary/80" title={args}>{args}</span>
        <span className="shrink-0 tabular-nums text-text-secondary/70">{formatDuration(step.duration_ms)}</span>
      </button>
      {step.error && step.outcome !== 'success' && (
        <div className={cn(
          'ml-[38px] mr-2 mb-1.5 text-[11px] break-words',
          step.outcome === 'denied' ? 'text-warning' : 'text-danger',
        )}>
          {step.error}
        </div>
      )}
      {open && (
        <div className="ml-[38px] mr-2 mb-2 space-y-1.5">
          {step.args && Object.keys(step.args).length > 0 && (
            <pre className="bg-bg/60 dark:bg-bg/60 border border-border dark:border-border rounded p-2 text-[11px] font-mono overflow-x-auto text-text-primary">
              {JSON.stringify(step.args, null, 2)}
            </pre>
          )}
          <pre className="bg-bg/60 dark:bg-bg/60 border border-border dark:border-border rounded p-2 text-[11px] font-mono max-h-64 overflow-auto whitespace-pre-wrap break-words text-text-primary">
            {output?.loading
              ? 'Loading output…'
              : output?.failed
                ? 'Output could not be loaded.'
                : (output?.text || 'No output recorded.')}
          </pre>
        </div>
      )}
    </div>
  );
}

/** What ran to produce one assistant reply: collapsed to a one-line summary. */
export function TurnTrace({ trace }: { trace: TurnTraceData }) {
  const [open, setOpen] = useState(false);
  const { steps, truncated } = trace;
  if (steps.length === 0) return null;

  const total = steps.length + truncated;
  const actors = [...new Set(steps.map(s => s.actor))];
  const failed = steps.filter(s => s.outcome === 'error').length;
  const denied = steps.filter(s => s.outcome === 'denied').length;
  const toolMs = steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0);

  return (
    <div className="mt-1.5 max-w-full w-full">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border/40 transition-colors"
        title={open ? 'Hide what ran' : 'Show what ran to produce this reply'}
      >
        <Wrench className="w-3 h-3" />
        <span>{total} {total === 1 ? 'action' : 'actions'}</span>
        <span className="text-text-secondary/50">·</span>
        <span className="truncate max-w-[220px]">{actors.join(', ')}</span>
        {toolMs > 0 && (
          <>
            <span className="text-text-secondary/50">·</span>
            <span className="tabular-nums" title="Total tool time">{formatDuration(toolMs)}</span>
          </>
        )}
        {failed > 0 && (
          <>
            <span className="text-text-secondary/50">·</span>
            <span className="text-danger">{failed} failed</span>
          </>
        )}
        {denied > 0 && (
          <>
            <span className="text-text-secondary/50">·</span>
            <span className="text-warning">{denied} denied</span>
          </>
        )}
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>
      {open && (
        <div className="mt-1 py-1 rounded-lg border border-border dark:border-border bg-surface/40 dark:bg-surface/40">
          {steps.map(step => <StepRow key={step.action_id} step={step} />)}
          {truncated > 0 && (
            <div className="px-3 py-1 text-[11px] text-text-secondary/70">
              +{truncated} more not shown. See the Actions page.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
