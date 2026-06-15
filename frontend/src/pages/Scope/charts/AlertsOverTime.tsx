import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import type { ScopeTimeseriesPoint } from '@/types';

export function AlertsOverTime({ data }: { data: ScopeTimeseriesPoint[] }) {
  const chartData = data.map((p) => ({ ...p, short: p.day.slice(5) }));
  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4">
      <h3 className="text-sm font-medium text-text-primary mb-3">Findings Over Time</h3>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" opacity={0.4} />
          <XAxis dataKey="short" tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }} tickLine={false} axisLine={false} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: 'rgb(var(--color-surface))', border: '1px solid rgb(var(--color-border))', borderRadius: 6, fontSize: 12 }}
            labelStyle={{ color: 'rgb(var(--color-text-secondary))' }}
          />
          <Line type="monotone" dataKey="count" stroke="rgb(var(--chart-1))" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
