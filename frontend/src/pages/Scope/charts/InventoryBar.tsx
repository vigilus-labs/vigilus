import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts';
import type { ScopeOverview } from '@/types';

/** Managed vs. discovered vs. monitored — a single comparative bar chart. */
export function InventoryBar({ overview }: { overview: ScopeOverview | null }) {
  const data = [
    { label: 'Managed', count: overview?.managed ?? 0, key: 'managed' },
    { label: 'Discovered', count: overview?.discovered_unique ?? 0, key: 'discovered' },
    { label: 'Unmanaged', count: overview?.unmanaged ?? 0, key: 'unmanaged' },
    { label: 'Findings', count: overview?.findings ?? 0, key: 'findings' },
  ];
  const colorFor: Record<string, string> = {
    managed: 'rgb(var(--chart-1))',
    discovered: 'rgb(var(--chart-5))',
    unmanaged: 'rgb(var(--chart-3))',
    findings: 'rgb(var(--chart-4))',
  };
  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4">
      <h3 className="text-sm font-medium text-text-primary mb-3">Inventory Snapshot</h3>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" opacity={0.4} vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }} tickLine={false} axisLine={false} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: 'rgb(var(--color-surface))', border: '1px solid rgb(var(--color-border))', borderRadius: 6, fontSize: 12 }}
            cursor={{ fill: 'rgb(var(--color-surface))', opacity: 0.5 }}
          />
          <Bar dataKey="count" radius={[3, 3, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.key} fill={colorFor[d.key]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
