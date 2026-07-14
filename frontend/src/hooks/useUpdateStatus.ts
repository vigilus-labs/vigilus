import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { UpdateStatus } from '@/types';

/** Shared status so sidebar, banner, and Settings stay in sync. */
let cached: UpdateStatus | null = null;
let pending: Promise<UpdateStatus> | null = null;
const listeners = new Set<(s: UpdateStatus) => void>();

function notify(s: UpdateStatus) {
  cached = s;
  for (const listener of listeners) listener(s);
}

function fetchStatus(): Promise<UpdateStatus> {
  if (cached) return Promise.resolve(cached);
  if (!pending) {
    pending = api
      .getUpdateStatus()
      .then((s) => {
        notify(s);
        return s;
      })
      .finally(() => {
        pending = null;
      });
  }
  return pending;
}

/**
 * Cached update-check from GET /system/update. Best-effort: failures leave
 * status null so callers can show a quiet version label with no glow.
 */
export function useUpdateStatus() {
  const [status, setStatus] = useState<UpdateStatus | null>(cached);

  useEffect(() => {
    const onUpdate = (s: UpdateStatus) => setStatus(s);
    listeners.add(onUpdate);

    let cancelled = false;
    fetchStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(() => {
        // best-effort; Settings exposes a manual re-check
      });

    return () => {
      cancelled = true;
      listeners.delete(onUpdate);
    };
  }, []);

  /** Replace cache after an explicit "Check for updates" from Settings. */
  const setStatusFromCheck = (s: UpdateStatus) => {
    notify(s);
  };

  return { status, setStatusFromCheck };
}
