import { useState, useEffect, useCallback } from 'react';
import {
  CalendarClock,
  Plus,
  Play,
  Pencil,
  Trash2,
  X,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  XCircle,
  Loader2,
  MinusCircle,
  Clock,
} from 'lucide-react';
import { api } from '@/lib/api';
import { ScheduledTask, Operator, ScheduleStatus } from '@/types';
import { useToast, useConfirm } from '@/components/Notifications';

// Friendly schedule builder. The user picks a frequency + time in plain
// language; we assemble a 5-field cron string (interpreted by the backend in
// the configured app timezone). "Custom" leaves the raw cron field exposed.
type Frequency = 'minutes' | 'hours' | 'daily' | 'weekly' | 'monthly' | 'custom';

const FREQUENCY_OPTIONS: { value: Frequency; label: string }[] = [
  { value: 'minutes', label: 'Every N minutes' },
  { value: 'hours', label: 'Every N hours' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'custom', label: 'Custom cron expression' },
];

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const EMPTY_FORM = {
  name: '',
  description: '',
  frequency: 'daily' as Frequency,
  interval: 15, // for minutes/hours
  time: '08:00', // HH:MM for daily/weekly/monthly
  weekday: 1, // 0=Sun..6=Sat (weekly)
  dayOfMonth: 1, // 1..28 (monthly)
  customCron: '',
  taskPrompt: '',
  operatorId: '',
  enabled: true,
};

type SchedForm = typeof EMPTY_FORM;

const clamp = (n: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, Number.isFinite(n) ? n : lo));

/** Assemble a 5-field cron expression from the structured form. */
function buildCron(f: SchedForm): string {
  const [hhRaw, mmRaw] = (f.time || '08:00').split(':');
  const hh = clamp(parseInt(hhRaw, 10), 0, 23);
  const mm = clamp(parseInt(mmRaw, 10), 0, 59);
  switch (f.frequency) {
    case 'minutes':
      return `*/${clamp(f.interval, 1, 59)} * * * *`;
    case 'hours':
      return `0 */${clamp(f.interval, 1, 23)} * * *`;
    case 'daily':
      return `${mm} ${hh} * * *`;
    case 'weekly':
      return `${mm} ${hh} * * ${clamp(f.weekday, 0, 6)}`;
    case 'monthly':
      return `${mm} ${hh} ${clamp(f.dayOfMonth, 1, 28)} * *`;
    case 'custom':
      return f.customCron.trim();
  }
}

/** Best-effort: recognise a cron string as one of the friendly frequencies. */
function parseCron(cron: string): Partial<SchedForm> {
  const c = cron.trim();
  let m: RegExpMatchArray | null;
  if ((m = c.match(/^\*\/(\d+) \* \* \* \*$/)))
    return { frequency: 'minutes', interval: parseInt(m[1], 10) };
  if ((m = c.match(/^0 \*\/(\d+) \* \* \*$/)))
    return { frequency: 'hours', interval: parseInt(m[1], 10) };
  const hhmm = (h: string, mn: string) =>
    `${mn.padStart(2, '0')}`.slice(0, 2) && `${h.padStart(2, '0')}:${mn.padStart(2, '0')}`;
  if ((m = c.match(/^(\d+) (\d+) \* \* \*$/)))
    return { frequency: 'daily', time: hhmm(m[2], m[1]) };
  if ((m = c.match(/^(\d+) (\d+) \* \* (\d+)$/)))
    return { frequency: 'weekly', time: hhmm(m[2], m[1]), weekday: parseInt(m[3], 10) };
  if ((m = c.match(/^(\d+) (\d+) (\d+) \* \*$/)))
    return { frequency: 'monthly', time: hhmm(m[2], m[1]), dayOfMonth: parseInt(m[3], 10) };
  return { frequency: 'custom', customCron: c };
}

/** Human-readable summary of the current schedule. */
function describeSchedule(f: SchedForm, tz: string): string {
  const t = f.time || '08:00';
  switch (f.frequency) {
    case 'minutes':
      return `Every ${clamp(f.interval, 1, 59)} minute(s)`;
    case 'hours':
      return `Every ${clamp(f.interval, 1, 23)} hour(s)`;
    case 'daily':
      return `Every day at ${t} (${tz})`;
    case 'weekly':
      return `Every ${WEEKDAYS[clamp(f.weekday, 0, 6)]} at ${t} (${tz})`;
    case 'monthly':
      return `On day ${clamp(f.dayOfMonth, 1, 28)} of each month at ${t} (${tz})`;
    case 'custom':
      return f.customCron.trim() ? `Custom: ${f.customCron.trim()}` : 'Enter a cron expression';
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function StatusBadge({ status }: { status: ScheduleStatus | null }) {
  if (!status) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-text-secondary dark:text-text-secondary">
        <Clock className="w-3 h-3" /> never run
      </span>
    );
  }
  const styles: Record<ScheduleStatus, { cls: string; icon: React.ReactNode; label: string }> = {
    success: {
      cls: 'text-green-600 dark:text-green-400 bg-green-500/10',
      icon: <CheckCircle2 className="w-3 h-3" />,
      label: 'success',
    },
    error: {
      cls: 'text-red-600 dark:text-red-400 bg-red-500/10',
      icon: <XCircle className="w-3 h-3" />,
      label: 'error',
    },
    running: {
      cls: 'text-blue-600 dark:text-blue-400 bg-blue-500/10',
      icon: <Loader2 className="w-3 h-3 animate-spin" />,
      label: 'running',
    },
    skipped: {
      cls: 'text-text-secondary dark:text-text-secondary bg-surface',
      icon: <MinusCircle className="w-3 h-3" />,
      label: 'skipped',
    },
  };
  const s = styles[status];
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium ${s.cls}`}>
      {s.icon} {s.label}
    </span>
  );
}

export default function Tasks() {
  const toast = useToast();
  const confirm = useConfirm();

  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [timezone, setTimezone] = useState('UTC');

  // Modal state
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const fetchTasks = useCallback(async () => {
    try {
      const data = await api.listSchedules();
      setTasks(data);
    } catch (err) {
      console.error('Failed to load scheduled tasks', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTasks();
    api.listOperators().then(setOperators).catch(() => {});
    api
      .getOrchestratorConfig()
      .then((cfg) => setTimezone(cfg.timezone || 'UTC'))
      .catch(() => {});
    // Poll so "running" status and results update while a task executes
    const interval = setInterval(fetchTasks, 8000);
    return () => clearInterval(interval);
  }, [fetchTasks]);

  const openCreate = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setIsModalOpen(true);
  };

  const openEdit = (task: ScheduledTask) => {
    setEditingId(task.id);
    setForm({
      ...EMPTY_FORM,
      name: task.name,
      description: task.description || '',
      ...parseCron(task.cron_expression),
      taskPrompt: task.task_prompt,
      operatorId: task.operator_id || '',
      enabled: task.enabled,
    });
    setIsModalOpen(true);
  };

  const handleSave = async () => {
    const cron = buildCron(form);
    if (!form.name.trim() || !cron || !form.taskPrompt.trim()) {
      toast('Name, schedule, and task prompt are required.', 'error');
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        cron_expression: cron,
        task_prompt: form.taskPrompt.trim(),
        operator_id: form.operatorId || null,
        enabled: form.enabled,
      };
      if (editingId) {
        await api.updateSchedule(editingId, payload);
        toast('Task updated', 'success');
      } else {
        await api.createSchedule(payload);
        toast('Task created', 'success');
      }
      setIsModalOpen(false);
      fetchTasks();
    } catch (err: any) {
      toast(`Failed to save task: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (task: ScheduledTask) => {
    const ok = await confirm({
      title: 'Delete scheduled task',
      message: `Delete "${task.name}"? Future runs will stop. Past chat sessions are kept.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteSchedule(task.id);
      toast('Task deleted', 'success');
      fetchTasks();
    } catch (err: any) {
      toast(`Failed to delete: ${err.message}`, 'error');
    }
  };

  const handleToggle = async (task: ScheduledTask) => {
    try {
      await api.updateSchedule(task.id, { enabled: !task.enabled });
      fetchTasks();
    } catch (err: any) {
      toast(`Failed to update: ${err.message}`, 'error');
    }
  };

  const handleRunNow = async (task: ScheduledTask) => {
    try {
      await api.runScheduleNow(task.id);
      toast(`Running "${task.name}" — check back shortly or watch the Chat page.`, 'info');
      fetchTasks();
    } catch (err: any) {
      toast(`Failed to run: ${err.message}`, 'error');
    }
  };

  return (
    <div>
      {/* Page header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-[20px] font-medium text-text-primary dark:text-text-primary tracking-[-0.02em]">
            Tasks
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mt-1">
            Recurring tasks sent to the Vigilus orchestrator on a schedule. Each run creates a
            chat session you can review. Times are in{' '}
            <span className="font-medium text-text-primary dark:text-text-primary">{timezone}</span>{' '}
            (<a href="/settings" className="text-accent hover:underline">change</a>).
          </p>
        </div>
        <button
          onClick={openCreate}
          className="flex items-center gap-1.5 px-3 py-2 rounded-md text-[13px] font-medium bg-accent text-white hover:bg-accent-hover transition-colors"
        >
          <Plus className="w-4 h-4" />
          New Task
        </button>
      </div>

      {/* Task list */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-text-secondary">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : tasks.length === 0 ? (
        <div className="border border-border dark:border-border rounded-card bg-white dark:bg-surface">
          <div className="flex flex-col items-center justify-center py-16 px-6">
            <div className="w-12 h-12 rounded-full bg-surface dark:bg-surface flex items-center justify-center mb-4">
              <CalendarClock className="w-6 h-6 text-text-secondary/30" strokeWidth={1.5} />
            </div>
            <p className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-1">
              No scheduled tasks yet
            </p>
            <p className="text-[13px] text-text-secondary dark:text-text-secondary text-center max-w-[380px] mb-4">
              Create a recurring task — e.g. "Pull the daily Wazuh alerts and report anything
              suspicious" every morning at 8 AM.
            </p>
            <button
              onClick={openCreate}
              className="flex items-center gap-1.5 px-3 py-2 rounded-md text-[13px] font-medium bg-accent text-white hover:bg-accent-hover transition-colors"
            >
              <Plus className="w-4 h-4" />
              Create your first task
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map((task) => {
            const expanded = expandedId === task.id;
            return (
              <div
                key={task.id}
                className="border border-border dark:border-border rounded-card bg-white dark:bg-surface"
              >
                <div className="px-4 py-3 flex items-center gap-3">
                  {/* Expand toggle */}
                  <button
                    onClick={() => setExpandedId(expanded ? null : task.id)}
                    className="p-1 rounded text-text-secondary hover:text-text-primary transition-colors shrink-0"
                  >
                    {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                  </button>

                  {/* Name + schedule */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-[14px] font-medium truncate ${task.enabled ? 'text-text-primary dark:text-text-primary' : 'text-text-secondary line-through'}`}>
                        {task.name}
                      </span>
                      <StatusBadge status={task.last_status} />
                    </div>
                    <div className="text-[12px] text-text-secondary dark:text-text-secondary mt-0.5 truncate">
                      <span className="font-mono">{task.cron_expression}</span>
                      <span className="mx-1.5">·</span>
                      next: {task.enabled ? formatTime(task.next_run_at) : 'disabled'}
                      <span className="mx-1.5">·</span>
                      last: {formatTime(task.last_run_at)}
                      {task.run_count > 0 && (
                        <>
                          <span className="mx-1.5">·</span>
                          {task.run_count} run{task.run_count === 1 ? '' : 's'}
                        </>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 shrink-0">
                    {/* Enabled toggle */}
                    <button
                      onClick={() => handleToggle(task)}
                      className={`relative w-9 h-5 rounded-full transition-colors mr-1 ${
                        task.enabled ? 'bg-accent' : 'bg-border dark:bg-border'
                      }`}
                      title={task.enabled ? 'Disable' : 'Enable'}
                    >
                      <span
                        className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
                          task.enabled ? 'left-[18px]' : 'left-0.5'
                        }`}
                      />
                    </button>
                    <button
                      onClick={() => handleRunNow(task)}
                      disabled={task.last_status === 'running'}
                      className="p-2 rounded text-text-secondary hover:text-green-600 hover:bg-green-500/5 disabled:opacity-40 transition-colors"
                      title="Run now"
                    >
                      <Play className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => openEdit(task)}
                      className="p-2 rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors"
                      title="Edit"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(task)}
                      className="p-2 rounded text-text-secondary hover:text-danger hover:bg-danger/5 transition-colors"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Expanded detail */}
                {expanded && (
                  <div className="px-12 pb-4 space-y-3 border-t border-border dark:border-border pt-3">
                    {task.description && (
                      <p className="text-[13px] text-text-secondary dark:text-text-secondary">
                        {task.description}
                      </p>
                    )}
                    <div>
                      <div className="text-[11px] font-medium text-text-secondary uppercase tracking-wider mb-1">
                        Task prompt
                      </div>
                      <div className="text-[13px] text-text-primary dark:text-text-primary bg-surface/50 dark:bg-surface/50 border border-border dark:border-border rounded px-3 py-2 whitespace-pre-wrap">
                        {task.task_prompt}
                      </div>
                    </div>
                    {task.last_result && (
                      <div>
                        <div className="text-[11px] font-medium text-text-secondary uppercase tracking-wider mb-1">
                          Last result
                        </div>
                        <div className="text-[13px] bg-surface/50 dark:bg-surface/50 border border-border dark:border-border rounded px-3 py-2 whitespace-pre-wrap text-text-primary dark:text-text-primary">
                          {task.last_result.error ? (
                            <span className="text-red-600 dark:text-red-400">{task.last_result.error}</span>
                          ) : (
                            task.last_result.summary || 'No summary available'
                          )}
                        </div>
                        {task.last_result.session_id && (
                          <div className="text-[12px] text-text-secondary mt-1.5">
                            Full transcript is on the{' '}
                            <a href="/chat" className="text-accent hover:underline">Chat page</a>{' '}
                            (session "⏰ {task.name} — …").
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Create / Edit modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-[560px] max-h-[90vh] overflow-y-auto rounded-card bg-white dark:bg-surface border border-border dark:border-border shadow-xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border dark:border-border">
              <h2 className="text-[15px] font-medium text-text-primary dark:text-text-primary">
                {editingId ? 'Edit Task' : 'New Scheduled Task'}
              </h2>
              <button
                onClick={() => setIsModalOpen(false)}
                className="p-1.5 rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="px-5 py-4 space-y-4">
              {/* Name */}
              <div>
                <label className="block text-[12px] font-medium text-text-secondary mb-1.5">
                  Name *
                </label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Daily security summary"
                  className="input w-full"
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-[12px] font-medium text-text-secondary mb-1.5">
                  Description
                </label>
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="What this task is for (optional)"
                  className="input w-full"
                />
              </div>

              {/* Schedule builder */}
              <div className="space-y-3">
                <label className="block text-[12px] font-medium text-text-secondary">
                  Schedule * <span className="font-normal text-text-secondary/70">({timezone})</span>
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={form.frequency}
                    onChange={(e) =>
                      setForm({ ...form, frequency: e.target.value as Frequency })
                    }
                    className="input flex-1 min-w-[160px]"
                  >
                    {FREQUENCY_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>

                  {(form.frequency === 'minutes' || form.frequency === 'hours') && (
                    <span className="flex items-center gap-1.5">
                      <span className="text-[12px] text-text-secondary">every</span>
                      <input
                        type="number"
                        min={1}
                        max={form.frequency === 'minutes' ? 59 : 23}
                        value={form.interval}
                        onChange={(e) =>
                          setForm({ ...form, interval: parseInt(e.target.value, 10) || 1 })
                        }
                        className="input w-20"
                      />
                      <span className="text-[12px] text-text-secondary">
                        {form.frequency === 'minutes' ? 'min' : 'hr'}
                      </span>
                    </span>
                  )}

                  {form.frequency === 'weekly' && (
                    <select
                      value={form.weekday}
                      onChange={(e) =>
                        setForm({ ...form, weekday: parseInt(e.target.value, 10) })
                      }
                      className="input min-w-[130px]"
                    >
                      {WEEKDAYS.map((d, i) => (
                        <option key={d} value={i}>{d}</option>
                      ))}
                    </select>
                  )}

                  {form.frequency === 'monthly' && (
                    <span className="flex items-center gap-1.5">
                      <span className="text-[12px] text-text-secondary">on day</span>
                      <input
                        type="number"
                        min={1}
                        max={28}
                        value={form.dayOfMonth}
                        onChange={(e) =>
                          setForm({ ...form, dayOfMonth: parseInt(e.target.value, 10) || 1 })
                        }
                        className="input w-20"
                      />
                    </span>
                  )}

                  {(form.frequency === 'daily' ||
                    form.frequency === 'weekly' ||
                    form.frequency === 'monthly') && (
                    <span className="flex items-center gap-1.5">
                      <span className="text-[12px] text-text-secondary">at</span>
                      <input
                        type="time"
                        value={form.time}
                        onChange={(e) => setForm({ ...form, time: e.target.value })}
                        className="input w-[120px]"
                      />
                    </span>
                  )}
                </div>

                {form.frequency === 'custom' && (
                  <div>
                    <input
                      type="text"
                      value={form.customCron}
                      onChange={(e) => setForm({ ...form, customCron: e.target.value })}
                      placeholder="0 8 * * *"
                      className="input w-full font-mono"
                    />
                    <p className="text-[11px] text-text-secondary/70 mt-1">
                      minute hour day month weekday
                    </p>
                  </div>
                )}

                <p className="text-[12px] text-text-secondary">
                  {describeSchedule(form, timezone)}
                  <span className="mx-1.5">·</span>
                  <span className="font-mono text-text-secondary/80">{buildCron(form) || '—'}</span>
                </p>
              </div>

              {/* Prompt */}
              <div>
                <label className="block text-[12px] font-medium text-text-secondary mb-1.5">
                  Task prompt (sent to Vigilus) *
                </label>
                <textarea
                  value={form.taskPrompt}
                  onChange={(e) => setForm({ ...form, taskPrompt: e.target.value })}
                  placeholder={'Pull the last 24 hours of Wazuh alerts. Investigate anything level 10+ and report suspicious activity. If outdated packages are found, list the affected servers.'}
                  rows={5}
                  className="input w-full resize-y"
                />
              </div>

              {/* Operator hint */}
              <div>
                <label className="block text-[12px] font-medium text-text-secondary mb-1.5">
                  Preferred operator (optional hint)
                </label>
                <select
                  value={form.operatorId}
                  onChange={(e) => setForm({ ...form, operatorId: e.target.value })}
                  className="input w-full"
                >
                  <option value="">Let Vigilus decide</option>
                  {operators.filter((o) => o.enabled).map((o) => (
                    <option key={o.id} value={o.id}>{o.name}</option>
                  ))}
                </select>
              </div>

              {/* Enabled */}
              <label className="flex items-center gap-2 text-[13px] text-text-primary dark:text-text-primary cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                  className="rounded border-border"
                />
                Enabled
              </label>
            </div>

            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border dark:border-border">
              <button
                onClick={() => setIsModalOpen(false)}
                className="px-3 py-2 rounded-md text-[13px] text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-1.5 px-3 py-2 rounded-md text-[13px] font-medium bg-accent text-white hover:bg-accent-hover disabled:opacity-50 transition-colors"
              >
                {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {editingId ? 'Save changes' : 'Create task'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
