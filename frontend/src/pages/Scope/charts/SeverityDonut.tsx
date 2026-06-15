import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import type { ScopeSeverityBucket } from '@/types';
import { severityColor } from '../colors';

export function SeverityDonut({ data }: { data: ScopeSeverityBucket[] }) {
  const total = data.reduce((s, d) => s + d.count, 0);
  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4">
      <h3 className="text-sm font-medium text-text-primary mb-3">Findings by Severity</h3>
      {total === 0 ? (
        <div className="h-[180px] flex items-center justify-center text-text-secondary text-sm">No findings</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <PieChart>
            <Pie data={data} dataKey="count" nameKey="severity" innerRadius={45} outerRadius={75} paddingAngle={2}>
              {data.map((d) => (
                <Cell key={d.severity} fill={severityColor(d.severity)} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{ background: 'rgb(var(--color-surface))', border: '1px solid rgb(var(--color-border))', borderRadius: 6, fontSize: 12 }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} formatter={(v) => <span style={{ color: 'rgb(var(--color-text-secondary))' }}>{v}</span>} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
