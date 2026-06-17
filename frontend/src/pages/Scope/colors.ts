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

/** Fixed palette of distinct, theme-appropriate colors used as the default
 * border/tint for a network segment (VLAN-proxy) group when no manual color
 * override exists. Keeps segments visually distinguishable with zero setup.
 * Hex so it can feed both `border` and rgba tints, matching the stored
 * override format (`ScopeSegment.color`). */
const SEGMENT_PALETTE = [
  '#6366f1', // indigo
  '#0891b2', // cyan
  '#16a34a', // green
  '#d97706', // amber
  '#dc2626', // red
  '#7c3aed', // violet
  '#db2777', // pink
  '#0d9488', // teal
];

export function segmentColor(index: number): string {
  const n = ((index % SEGMENT_PALETTE.length) + SEGMENT_PALETTE.length) % SEGMENT_PALETTE.length;
  return SEGMENT_PALETTE[n];
}

/** Hex → `rgba(r,g,b,a)` tint helper for segment backgrounds. Falls back to
 * transparent for non-hex input. */
export function hexToRgba(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return 'transparent';
  const r = parseInt(m[1].slice(0, 2), 16);
  const g = parseInt(m[1].slice(2, 4), 16);
  const b = parseInt(m[1].slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
