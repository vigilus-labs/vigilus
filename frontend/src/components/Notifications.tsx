import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { CheckCircle, XCircle, Info, X, AlertTriangle } from 'lucide-react';

// ─── Types ────────────────────────────────────────────────────────────────────

type ToastType = 'success' | 'error' | 'info';

interface Toast {
  id: number;
  type: ToastType;
  message: string;
}

interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

interface ConfirmState extends ConfirmOptions {
  resolve: (confirmed: boolean) => void;
}

interface NotificationsContextValue {
  toast: (message: string, type?: ToastType) => void;
  confirm: (options: ConfirmOptions | string) => Promise<boolean>;
}

const NotificationsContext = createContext<NotificationsContextValue | null>(null);

// ─── Hooks ────────────────────────────────────────────────────────────────────

export function useToast() {
  const ctx = useContext(NotificationsContext);
  if (!ctx) throw new Error('useToast must be used within NotificationsProvider');
  return ctx.toast;
}

export function useConfirm() {
  const ctx = useContext(NotificationsContext);
  if (!ctx) throw new Error('useConfirm must be used within NotificationsProvider');
  return ctx.confirm;
}

// ─── Toast item ───────────────────────────────────────────────────────────────

const TOAST_ICONS: Record<ToastType, typeof CheckCircle> = {
  success: CheckCircle,
  error: XCircle,
  info: Info,
};

const TOAST_COLORS: Record<ToastType, string> = {
  success: 'text-success',
  error: 'text-danger',
  info: 'text-info',
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const Icon = TOAST_ICONS[toast.type];
  return (
    <div
      role="status"
      className="flex items-start w-80 bg-white border border-border rounded-card shadow-lg p-3 animate-toast-in"
    >
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${TOAST_COLORS[toast.type]}`} />
      <p className="text-[13px] text-text-primary mx-2.5 flex-1 break-words">{toast.message}</p>
      <button
        onClick={() => onDismiss(toast.id)}
        className="p-0.5 text-text-secondary hover:text-text-primary rounded transition-colors shrink-0"
        aria-label="Dismiss"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ─── Confirm dialog ───────────────────────────────────────────────────────────

function ConfirmDialog({ state, onClose }: { state: ConfirmState; onClose: (confirmed: boolean) => void }) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const danger = state.danger ?? true;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30" onClick={() => onClose(false)} />
      <div
        role="alertdialog"
        aria-modal="true"
        className="relative bg-white border border-border rounded-card shadow-xl w-[400px] max-w-[calc(100vw-2rem)] p-5 animate-toast-in"
      >
        <div className="flex items-start">
          {danger && (
            <div className="w-8 h-8 rounded-full bg-danger/10 flex items-center justify-center mr-3 shrink-0">
              <AlertTriangle className="w-4 h-4 text-danger" />
            </div>
          )}
          <div className="flex-1">
            <h3 className="text-sm font-medium text-text-primary">
              {state.title ?? 'Are you sure?'}
            </h3>
            <p className="text-[13px] text-text-secondary mt-1.5">{state.message}</p>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={() => onClose(false)}
            className="px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary hover:bg-surface rounded-md transition-colors"
          >
            {state.cancelLabel ?? 'Cancel'}
          </button>
          <button
            ref={confirmRef}
            onClick={() => onClose(true)}
            className={`px-3 py-1.5 text-sm text-white rounded-md transition-colors ${
              danger ? 'bg-danger hover:bg-danger/90' : 'bg-text-primary hover:bg-text-primary/90'
            }`}
          >
            {state.confirmLabel ?? 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Provider ─────────────────────────────────────────────────────────────────

let nextToastId = 1;

export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, type: ToastType = 'info') => {
      const id = nextToastId++;
      setToasts((prev) => [...prev, { id, type, message }]);
      setTimeout(() => dismiss(id), 5000);
    },
    [dismiss],
  );

  const confirm = useCallback((options: ConfirmOptions | string) => {
    const opts = typeof options === 'string' ? { message: options } : options;
    return new Promise<boolean>((resolve) => {
      setConfirmState({ ...opts, resolve });
    });
  }, []);

  const closeConfirm = useCallback(
    (confirmed: boolean) => {
      setConfirmState((prev) => {
        prev?.resolve(confirmed);
        return null;
      });
    },
    [],
  );

  return (
    <NotificationsContext.Provider value={{ toast, confirm }}>
      {children}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none [&>*]:pointer-events-auto">
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
      {confirmState && <ConfirmDialog state={confirmState} onClose={closeConfirm} />}
    </NotificationsContext.Provider>
  );
}
