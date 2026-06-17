import { useEffect, useState } from 'react';
import { X, Check } from 'lucide-react';
import { api } from '@/lib/api';
import type { ScopeSegment } from '@/types';

/** Inline modal for editing a network segment's cosmetic override (label/color).

 * Triggered by clicking a segment group header in the topology. Owns its own
 * form state; calls the upsert endpoint then notifies the parent to refresh. */
export function SegmentEditor({
  cidr,
  initial,
  onClose,
  onSaved,
}: {
  cidr: string;
  initial: ScopeSegment | undefined;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [label, setLabel] = useState(initial?.label ?? '');
  const [color, setColor] = useState(initial?.color ?? '#6366f1');
  // <input type="color"> can't represent "unset", so we track whether the
  // user actually moved the picker. A label-only edit must NOT persist a color
  // and clobber the auto-palette color this segment otherwise uses.
  const [colorTouched, setColorTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset form state if the target segment changes while the modal is open.
  useEffect(() => {
    setLabel(initial?.label ?? '');
    setColor(initial?.color ?? '#6366f1');
    setColorTouched(false);
    setError(null);
  }, [cidr, initial]);

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.scopeSetSegment({
        cidr,
        label: label.trim() || null,
        color: colorTouched ? color : null,
      });
      onSaved();
      onClose();
    } catch {
      setError('Failed to save segment.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-[340px] bg-white dark:bg-surface border border-border rounded-card shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between h-12 px-4 border-b border-border">
          <span className="text-sm font-medium text-text-primary truncate">Edit segment</span>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-4 space-y-4">
          <div className="text-[11px] font-mono text-text-secondary break-all">{cidr}</div>

          <label className="block">
            <span className="text-[11px] font-semibold uppercase text-text-secondary">Label</span>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. VLAN 10 — IoT"
              className="mt-1 w-full px-2.5 py-1.5 text-[13px] rounded-md border border-border bg-bg text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </label>

          <label className="block">
            <span className="text-[11px] font-semibold uppercase text-text-secondary">Color</span>
            <div className="mt-1 flex items-center gap-2">
              <input
                type="color"
                value={color}
                onChange={(e) => {
                  setColor(e.target.value);
                  setColorTouched(true);
                }}
                className="w-9 h-9 rounded-md border border-border bg-bg cursor-pointer p-0.5"
              />
              <span className="text-[12px] font-mono text-text-secondary">{color}</span>
            </div>
          </label>

          {error && <p className="text-[11px] text-danger">{error}</p>}
        </div>
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border">
          <button
            onClick={onClose}
            className="text-[12px] px-3 py-1.5 rounded-md text-text-secondary hover:bg-bg"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={saving}
            className="inline-flex items-center gap-1 text-[12px] px-3 py-1.5 rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50"
          >
            <Check className="w-3.5 h-3.5" />
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
