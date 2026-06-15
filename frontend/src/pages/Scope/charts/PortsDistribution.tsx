import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts';
import type { ScopePortBucket } from '@/types';
import { chartColor } from '../colors';

export function PortsDistribution({ data }: { data: ScopePortBucket[] }) {
  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4">
      <h3 className="text-sm font-medium text-text-primary mb-3">Top Open Services</h3>
      {data.length === 0 ? (
        <div className="h-[180px] flex items-center justify-center text-text-secondary text-sm">No scan data</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 12, bottom: 0, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" opacity={0.4} horizontal={false} />
            <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }} tickLine={false} axisLine={false} />
            <YAxis type="category" dataKey="service" width={64} tick={{ fontSize: 11, fill: 'rgb(var(--color-text-secondary))' }} tickLine={false} axisLine={false} />
            <Tooltip
              contentStyle={{ background: 'rgb(var(--color-surface))', border: '1px solid rgb(var(--color-border))', borderRadius: 6, fontSize: 12 }}
              cursor={{ fill: 'rgb(var(--color-surface))', opacity: 0.5 }}
            />
            <Bar dataKey="count" radius={[0, 3, 3, 0]}>
              {data.map((_, i) => (
                <Cell key={i} fill={chartColor(i)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
