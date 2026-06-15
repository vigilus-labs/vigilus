import { useState } from 'react';
import { Check, X, Loader2 } from 'lucide-react';

export interface JitGrantOpts {
  ttl_minutes?: number | null;
  single_use?: boolean;
  resource?: string | null;
}

interface Props {
  /** The resource the request was raised for; enables the scope toggle when not "*". */
  resource?: string;
  busy?: boolean;
  onApprove: (opts: JitGrantOpts) => void;
  onDeny: () => void;
}

const PRESETS: { label: string; opts: JitGrantOpts }[] = [
  { label: 'Just once', opts: { single_use: true } },
  { label: '15 min', opts: { ttl_minutes: 15 } },
  { label: '1 hour', opts: { ttl_minutes: 60 } },
];

/**
 * Lets the approver choose a JIT grant's blast radius: how long it lasts
 * ("just once" = single command, a preset window, or a custom number of
 * minutes) and, when the request targets a specific resource, whether to
 * limit the grant to it or broaden it to anything.
 */
export function JitGrantControls({ resource, busy, onApprove, onDeny }: Props) {
  const [sel, setSel] = useState(1); // default: 15 min
  const [customMin, setCustomMin] = useState('30');
  const [thisOnly, setThisOnly] = useState(true);
  const scoped = !!resource && resource !== '*';

  const chip = (active: boolean) =>
    `px-2.5 py-1 text-[12px] rounded-md border transition-colors ${
      active
        ? 'border-accent bg-accent/10 text-accent font-medium'
        : 'border-border dark:border-border text-text-secondary hover:text-text-primary'
    }`;

  const buildOpts = (): JitGrantOpts => {
    const base: JitGrantOpts =
      sel === -1
        ? { ttl_minutes: Math.max(1, parseInt(customMin, 10) || 15) }
        : PRESETS[sel].opts;
    return { ...base, resource: scoped ? (thisOnly ? resource : '*') : undefined };
  };

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-text-secondary mr-0.5">Grant for:</span>
        {PRESETS.map((p, i) => (
          <button key={p.label} type="button" onClick={() => setSel(i)} className={chip(i === sel)}>
            {p.label}
          </button>
        ))}
        <button type="button" onClick={() => setSel(-1)} className={chip(sel === -1)}>
          Custom
        </button>
        {sel === -1 && (
          <span className="flex items-center gap-1">
            <input
              type="number"
              min={1}
              value={customMin}
              onChange={e => setCustomMin(e.target.value)}
              className="w-16 px-2 py-1 text-[12px] rounded-md border border-border dark:border-border bg-white dark:bg-surface text-text-primary font-mono"
            />
            <span className="text-[11px] text-text-secondary">min</span>
          </span>
        )}
      </div>

      {scoped && (
        <label className="flex items-center gap-2 text-[11px] text-text-secondary cursor-pointer">
          <input type="checkbox" checked={thisOnly} onChange={e => setThisOnly(e.target.checked)} />
          Limit to <span className="font-mono text-text-primary dark:text-text-primary">{resource}</span>
          <span className="text-text-secondary/60">(uncheck to allow any resource)</span>
        </label>
      )}

      <div className="flex items-center gap-2 pt-0.5">
        <button
          type="button"
          onClick={() => onApprove(buildOpts())}
          disabled={busy}
          className="flex items-center gap-1 px-3 py-1.5 text-[12px] font-medium rounded bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
          Approve
        </button>
        <button
          type="button"
          onClick={onDeny}
          disabled={busy}
          className="flex items-center gap-1 px-3 py-1.5 text-[12px] font-medium rounded border border-border dark:border-border text-text-secondary hover:text-danger hover:border-danger/40 disabled:opacity-50 transition-colors"
        >
          <X className="w-3.5 h-3.5" /> Deny
        </button>
      </div>
    </div>
  );
}
