import type { UsageWindow } from '@/types';

/** Token counts: grouped by default, compact (12.3K / 4.5M) for axis ticks. */
export function formatTokens(n: number, opts?: { compact?: boolean }): string {
  if (!opts?.compact) return n.toLocaleString();
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`;
  return String(n);
}

/** Estimated cost. Sub-cent amounts keep 4 decimals so they aren't shown as $0.00. */
export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return '—';
  if (usd > 0 && usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

/**
 * Axis label for a series bucket. Buckets are local-time strings from the API:
 * `YYYY-MM-DDTHH:00` for the hourly `today` window, `YYYY-MM-DD` otherwise.
 */
export function shortBucketLabel(bucket: string, window: UsageWindow): string {
  if (window === 'today') return bucket.slice(11, 16);
  return bucket.slice(5);
}

/** Share of a total, guarding the empty-window divide-by-zero. */
export function percentOf(value: number, total: number): number {
  return total > 0 ? (value / total) * 100 : 0;
}
