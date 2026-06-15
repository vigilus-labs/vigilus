import type { RunningTask, RunningTaskActivity } from '@/types';

export interface OperatorStatus {
  running: boolean;
  /** Running turns currently attributed to this operator (most recent first). */
  tasks: RunningTask[];
  /** Best single-line description of what the operator is doing right now. */
  currentStep: string | null;
}

/**
 * Cross-reference the live task registry with the operator list.
 *
 * A turn reports its currently-delegated operator via `task.operator`, so an
 * operator is "running" iff some in-flight turn names it. Process-local, same
 * semantics as the /running-tasks endpoint.
 */
export function buildOperatorStatus(
  runningTasks: RunningTask[],
): Map<string, OperatorStatus> {
  const byOperator = new Map<string, RunningTask[]>();
  for (const t of runningTasks) {
    if (!t.operator) continue;
    const list = byOperator.get(t.operator) ?? [];
    list.push(t);
    byOperator.set(t.operator, list);
  }

  const status = new Map<string, OperatorStatus>();
  for (const [name, tasks] of byOperator) {
    tasks.sort((a, b) => b.elapsed_seconds - a.elapsed_seconds);
    status.set(name, {
      running: true,
      tasks,
      currentStep: tasks[0]?.current_step ?? null,
    });
  }
  return status;
}

export function statusFor(
  statusMap: Map<string, OperatorStatus>,
  operatorName: string,
): OperatorStatus {
  return (
    statusMap.get(operatorName) ?? { running: false, tasks: [], currentStep: null }
  );
}

// ── Activity feed presentation ───────────────────────────────────────────────

export interface ActivityView {
  label: string;
  detail: string | null;
  tone: 'tool' | 'result' | 'delegation' | 'thinking' | 'error' | 'text';
}

/** Turn a buffered activity event into a compact, human-readable feed row. */
export function describeActivity(ev: RunningTaskActivity): ActivityView {
  const d = ev.data || {};
  switch (ev.type) {
    case 'tool_call':
      return { label: `Calling ${d.tool ?? 'tool'}`, detail: d.operator ?? null, tone: 'tool' };
    case 'tool_result':
      return {
        label: `${d.tool ?? 'tool'} ${d.success === false ? 'failed' : 'returned'}`,
        detail: typeof d.preview === 'string' ? d.preview : null,
        tone: 'result',
      };
    case 'delegation_start':
      return {
        label: `Delegating to ${d.operator ?? 'operator'}`,
        detail: typeof d.task === 'string' ? d.task : null,
        tone: 'delegation',
      };
    case 'delegation_result':
      return {
        label: `${d.operator ?? 'Operator'} finished`,
        detail: typeof d.summary === 'string' ? d.summary : null,
        tone: 'delegation',
      };
    case 'thinking':
      return { label: 'Thinking…', detail: typeof d.text === 'string' ? d.text : null, tone: 'thinking' };
    case 'error':
      return { label: 'Error', detail: typeof d.message === 'string' ? d.message : String(d.error ?? ''), tone: 'error' };
    case 'text_delta':
      return { label: 'Responding', detail: typeof d.text === 'string' ? d.text : null, tone: 'text' };
    default:
      return { label: ev.type, detail: null, tone: 'text' };
  }
}
