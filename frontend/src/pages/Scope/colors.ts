/** Read a CSS-variable chart color as an `rgb(...)` string for recharts.

 * Colors are stored as "R G B" triplets in index.css so Tailwind's alpha slot
 * works; recharts wants concrete rgb() strings. Reads at runtime so dark-mode
 * toggles are honoured.
 */
export function chartColor(index: number): string {
  const n = ((index % 6) + 6) % 6 + 1;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(`--chart-${n}`)
    .trim();
  return `rgb(${raw})`;
}

export const SEVERITY_COLORS: Record<string, string> = {
  critical: 'rgb(var(--chart-4))',
  high: 'rgb(var(--chart-3))',
  medium: 'rgb(var(--chart-1))',
  low: 'rgb(var(--chart-2))',
  info: 'rgb(var(--chart-5))',
};

export function severityColor(severity: string): string {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(
      severity === 'critical'
        ? '--chart-4'
        : severity === 'high'
          ? '--chart-3'
          : severity === 'medium'
            ? '--chart-1'
            : severity === 'low'
              ? '--chart-2'
              : '--chart-5'
    )
    .trim();
  return `rgb(${raw})`;
}
