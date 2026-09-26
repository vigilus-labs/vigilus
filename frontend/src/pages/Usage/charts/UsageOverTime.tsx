import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { UsageSeriesPoint, UsageWindow } from '@/types';
import { formatTokens, shortBucketLabel } from '../format';

/** Stacked tokens over time: Vigilus, compression, and Operators. */
export function UsageOverTime({
  data,
  window,
}: {
  data: UsageSeriesPoint[];
  window: UsageWindow;
}) {
  const chartData = data.map((p) => ({
    ...p,
    short: shortBucketLabel(p.bucket, window),
  }));

  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4">
      <h3 className="text-sm font-medium text-text-primary mb-3">Tokens Over Time</h3>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" opacity={0.4} />
          <XAxis
            dataKey="short"
            tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }}
            tickLine={false}
            axisLine={false}
            minTickGap={16}
          />
          <YAxis
            allowDecimals={false}
            width={56}
            tickFormatter={(v: number) => formatTokens(v, { compact: true })}
            tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            formatter={(value, name) => [formatTokens(Number(value ?? 0)), name]}
            contentStyle={{
              background: 'rgb(var(--color-surface))',
              border: '1px solid rgb(var(--color-border))',
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: 'rgb(var(--color-text-secondary))' }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
          <Area
            type="monotone"
            stackId="tokens"
            dataKey="orchestrator_tokens"
            name="Vigilus"
            stroke="rgb(var(--chart-1))"
            fill="rgb(var(--chart-1))"
            fillOpacity={0.25}
            strokeWidth={2}
          />
          <Area
            type="monotone"
            stackId="tokens"
            dataKey="compression_tokens"
            name="Compression"
            stroke="rgb(var(--chart-6))"
            fill="rgb(var(--chart-6))"
            fillOpacity={0.25}
            strokeWidth={2}
          />
          <Area
            type="monotone"
            stackId="tokens"
            dataKey="operator_tokens"
            name="Operators"
            stroke="rgb(var(--chart-5))"
            fill="rgb(var(--chart-5))"
            fillOpacity={0.25}
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
