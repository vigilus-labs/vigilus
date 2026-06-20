import { useEffect, useState } from 'react';
import { ArrowUpCircle, X, ExternalLink } from 'lucide-react';
import { api } from '@/lib/api';
import type { UpdateStatus } from '@/types';

const DISMISS_KEY = 'vigilus.update.dismissed';

/**
 * App-wide "a new version is available" banner. Floats at the bottom-right so
 * it never collides with the JIT approval banner (top-center). Checks once on
 * mount against the cached backend report and stays dismissed (per-version)
 * until a newer release appears.
 */
export function UpdateBanner() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getUpdateStatus()
      .then((s) => {
        if (cancelled) return;
        setStatus(s);
        if (s.update_available && s.latest_version) {
          setDismissed(localStorage.getItem(DISMISS_KEY) === s.latest_version);
        }
      })
      .catch(() => {
        // best-effort; the Settings page exposes a manual re-check
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status?.update_available || dismissed) return null;

  const dismiss = () => {
    if (status.latest_version) localStorage.setItem(DISMISS_KEY, status.latest_version);
    setDismissed(true);
  };

  return (
    <div className="fixed bottom-4 right-4 z-[100] w-[min(380px,calc(100vw-2rem))] pointer-events-none">
      <div className="pointer-events-auto rounded-card border border-accent/40 bg-white dark:bg-surface shadow-xl shadow-black/10 overflow-hidden">
        <div className="flex items-start gap-3 px-4 py-3">
          <ArrowUpCircle className="w-5 h-5 text-accent shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium text-text-primary">
              Vigilus {status.latest_version} is available
            </div>
            <div className="text-[12px] text-text-secondary">
              You're running {status.current_version}.
            </div>
            <div className="mt-2 flex items-center gap-3">
              {status.release_url && (
                <a
                  href={status.release_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:underline"
                >
                  Release notes <ExternalLink className="w-3 h-3" />
                </a>
              )}
              <code className="text-[11px] text-text-secondary truncate">
                {status.image}:v{status.latest_version}
              </code>
            </div>
          </div>
          <button
            onClick={dismiss}
            aria-label="Dismiss"
            className="shrink-0 text-text-secondary hover:text-text-primary"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
