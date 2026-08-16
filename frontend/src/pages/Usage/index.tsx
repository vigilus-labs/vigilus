import { useCallback, useEffect, useState } from 'react';
import { BarChart3, Bot, Coins, Cpu, Sparkles } from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { UsageByActor, UsageSummary, UsageWindow } from '@/types';
import { UsageOverTime } from './charts/UsageOverTime';
import { formatCost, formatTokens, percentOf } from './format';

const WINDOWS: { id: UsageWindow; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: '7d', label: '7 days' },
  { id: '30d', label: '30 days' },
  { id: 'all', label: 'All time' },
];

function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  hint?: string;
  color: string;
}) {
  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4 flex items-center gap-3">
      <div className={cn('w-9 h-9 rounded-full flex items-center justify-center shrink-0', color)}>
        <Icon className="w-4 h-4" />
      </div>
      <div className="min-w-0">
        <p className="text-[12px] text-text-secondary">{label}</p>
        <p className="text-lg font-medium text-text-primary leading-tight">{value}</p>
        {hint && <p className="text-[11px] text-text-secondary truncate">{hint}</p>}
      </div>
    </div>
  );
}

/** Horizontal share bar behind a row's label — reads as a mini bar chart. */
function ShareBar({ percent, tone }: { percent: number; tone: string }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-border/50 overflow-hidden">
      <div
        className={cn('h-full rounded-full', tone)}
        style={{ width: `${Math.max(percent, percent > 0 ? 2 : 0)}%` }}
      />
    </div>
  );
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4">
      <div className="mb-3">
        <h3 className="text-sm font-medium text-text-primary">{title}</h3>
        {subtitle && <p className="text-[11px] text-text-secondary mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

function isOrchestrator(row: UsageByActor): boolean {
  return row.actor_type === 'orchestrator';
}

export default function Usage() {
  const [usageWindow, setUsageWindow] = useState<UsageWindow>('7d');
  const [data, setData] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async (w: UsageWindow, signal: { cancelled: boolean }) => {
    setLoading(true);
    setError('');
    try {
      const summary = await api.getUsage(w);
      if (!signal.cancelled) setData(summary);
    } catch (err: any) {
      if (!signal.cancelled) setError(err?.message || 'Failed to load usage');
    } finally {
      if (!signal.cancelled) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const signal = { cancelled: false };
    load(usageWindow, signal);
    return () => {
      signal.cancelled = true;
    };
  }, [usageWindow, load]);

  const totals = data?.totals;
  const orchestrator = data?.by_actor.find(isOrchestrator);
  const operators = (data?.by_actor ?? []).filter((r) => !isOrchestrator(r));
  const operatorTokens = operators.reduce((sum, r) => sum + r.total_tokens, 0);
  const grandTotal = totals?.total_tokens ?? 0;
  const empty = !!data && grandTotal === 0;

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-medium text-text-primary mb-1">Token Usage</h1>
          <p className="text-text-secondary text-sm">
            LLM tokens and estimated cost, split between Vigilus itself and each Operator.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {WINDOWS.map((w) => (
            <button
              key={w.id}
              type="button"
              onClick={() => setUsageWindow(w.id)}
              className={cn(
                'px-3 py-1.5 text-xs rounded-md border transition-colors',
                usageWindow === w.id
                  ? 'bg-accent/10 text-accent border-accent/30 font-medium'
                  : 'border-border text-text-secondary hover:bg-surface'
              )}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}
      {loading && !data && <p className="text-sm text-text-secondary">Loading usage…</p>}

      {data && empty && (
        <div className="bg-white dark:bg-surface border border-border rounded-card p-10 text-center">
          <BarChart3 className="w-8 h-8 mx-auto mb-3 text-text-secondary" />
          <p className="text-sm text-text-primary mb-1">No usage recorded in this window</p>
          <p className="text-[12px] text-text-secondary">
            Send a message on the Chat page — every orchestrator and Operator completion is metered.
          </p>
        </div>
      )}

      {data && !empty && totals && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <StatTile
              icon={Cpu}
              label="Total tokens"
              value={formatTokens(totals.total_tokens)}
              hint={`${formatTokens(totals.input_tokens)} in · ${formatTokens(totals.output_tokens)} out`}
              color="bg-accent/10 text-accent"
            />
            <StatTile
              icon={Sparkles}
              label="Vigilus (orchestrator)"
              value={formatTokens(orchestrator?.total_tokens ?? 0)}
              hint={`${percentOf(orchestrator?.total_tokens ?? 0, grandTotal).toFixed(0)}% of tokens`}
              color="bg-chart-1/10 text-chart-1"
            />
            <StatTile
              icon={Bot}
              label="Operators"
              value={formatTokens(operatorTokens)}
              hint={`${operators.length} active · ${percentOf(operatorTokens, grandTotal).toFixed(0)}% of tokens`}
              color="bg-chart-5/10 text-chart-5"
            />
            <StatTile
              icon={Coins}
              label="Estimated cost"
              value={formatCost(totals.estimated_cost_usd)}
              hint={data.cost_incomplete ? 'Partial — some models unpriced' : 'List prices'}
              color="bg-chart-2/10 text-chart-2"
            />
          </div>

          <UsageOverTime data={data.series} window={usageWindow as UsageWindow} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Panel
              title="By actor"
              subtitle="Vigilus is the orchestrator; every other row is an Operator."
            >
              <div className="space-y-3">
                {data.by_actor.map((row) => {
                  const share = percentOf(row.total_tokens, grandTotal);
                  return (
                    <div key={`${row.actor_type}-${row.operator_id ?? 'orchestrator'}`}>
                      <div className="flex items-baseline justify-between gap-3 mb-1">
                        <span className="text-sm text-text-primary truncate flex items-center gap-1.5">
                          {isOrchestrator(row) ? (
                            <Sparkles className="w-3.5 h-3.5 text-chart-1 shrink-0" />
                          ) : (
                            <Bot className="w-3.5 h-3.5 text-chart-5 shrink-0" />
                          )}
                          {row.name}
                        </span>
                        <span className="text-[12px] text-text-secondary whitespace-nowrap">
                          {formatTokens(row.total_tokens)} · {formatCost(row.estimated_cost_usd)}
                        </span>
                      </div>
                      <ShareBar
                        percent={share}
                        tone={isOrchestrator(row) ? 'bg-chart-1' : 'bg-chart-5'}
                      />
                      <p className="text-[11px] text-text-secondary mt-1">
                        {share.toFixed(1)}% · {formatTokens(row.input_tokens)} in ·{' '}
                        {formatTokens(row.output_tokens)} out
                      </p>
                    </div>
                  );
                })}
              </div>
            </Panel>

            <Panel title="By model" subtitle="Which models the tokens went to.">
              {data.by_model.length === 0 ? (
                <p className="text-sm text-text-secondary">No model data.</p>
              ) : (
                <div className="space-y-3">
                  {data.by_model.slice(0, 8).map((row) => (
                    <div key={`${row.provider_type ?? 'none'}-${row.model ?? 'unknown'}`}>
                      <div className="flex items-baseline justify-between gap-3 mb-1">
                        <span className="text-sm text-text-primary truncate">{row.name}</span>
                        <span className="text-[12px] text-text-secondary whitespace-nowrap">
                          {formatTokens(row.total_tokens)} · {formatCost(row.estimated_cost_usd)}
                        </span>
                      </div>
                      <ShareBar
                        percent={percentOf(row.total_tokens, grandTotal)}
                        tone="bg-chart-3"
                      />
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Panel title="By provider">
              {data.by_provider.length === 0 ? (
                <p className="text-sm text-text-secondary">No provider data.</p>
              ) : (
                <div className="space-y-3">
                  {data.by_provider.map((row) => (
                    <div key={row.provider_type ?? row.name}>
                      <div className="flex items-baseline justify-between gap-3 mb-1">
                        <span className="text-sm text-text-primary truncate">{row.name}</span>
                        <span className="text-[12px] text-text-secondary whitespace-nowrap">
                          {formatTokens(row.total_tokens)} · {formatCost(row.estimated_cost_usd)}
                        </span>
                      </div>
                      <ShareBar
                        percent={percentOf(row.total_tokens, grandTotal)}
                        tone="bg-chart-2"
                      />
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            <Panel title="Heaviest sessions" subtitle="Chat sessions that used the most tokens.">
              {data.top_sessions.length === 0 ? (
                <p className="text-sm text-text-secondary">
                  No session-attributed usage in this window.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-[12px] text-text-secondary border-b border-border">
                        <th className="py-2 pr-3 font-medium">Session</th>
                        <th className="py-2 pr-3 font-medium text-right">Tokens</th>
                        <th className="py-2 font-medium text-right">Est. cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.top_sessions.map((row) => (
                        <tr key={row.session_id} className="border-b border-border/60 last:border-0">
                          <td className="py-2 pr-3 text-text-primary truncate max-w-[18rem]">
                            {row.title}
                          </td>
                          <td className="py-2 pr-3 text-text-secondary text-right whitespace-nowrap">
                            {formatTokens(row.total_tokens)}
                          </td>
                          <td className="py-2 text-text-secondary text-right whitespace-nowrap">
                            {formatCost(row.estimated_cost_usd)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>

          <p className="text-[11px] text-text-secondary">
            Costs are estimates from published list prices — OpenRouter's live price catalog, plus a
            static table for direct Anthropic/OpenAI/Google models (override it at{' '}
            <code className="font-mono">data/model_prices.json</code>). Models with no known price
            still count tokens but are excluded from the cost total.
          </p>
        </>
      )}
    </div>
  );
}
