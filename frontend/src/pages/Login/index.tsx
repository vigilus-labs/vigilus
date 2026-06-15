import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldCheck, ChevronDown, Copy, Check } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { ApiError } from '@/lib/api';

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }
  return (
    <button
      type="button"
      onClick={copy}
      className="ml-2 shrink-0 text-text-secondary dark:text-text-secondary hover:text-text-primary dark:hover:text-text-primary transition-colors"
      title="Copy"
    >
      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

function ResetPanel() {
  const bareMetal = 'vigilus user reset-password YOUR_USERNAME';
  const docker = 'docker exec -it vigilus vigilus user reset-password YOUR_USERNAME';

  return (
    <div className="mt-3 rounded-lg border border-border bg-white dark:bg-bg p-4 space-y-3">
      <p className="text-[12px] text-text-secondary dark:text-text-secondary">
        Password reset requires CLI access to the server. Run the command for your setup, then log in with the new password.
      </p>

      <div>
        <p className="text-[11px] font-medium text-text-secondary dark:text-text-secondary mb-1 uppercase tracking-wide">
          Bare metal
        </p>
        <div className="flex items-center justify-between rounded border border-border bg-surface dark:bg-surface px-2.5 py-1.5">
          <code className="text-[11px] font-mono text-text-primary dark:text-text-primary break-all">
            {bareMetal}
          </code>
          <CopyButton text={bareMetal} />
        </div>
      </div>

      <div>
        <p className="text-[11px] font-medium text-text-secondary dark:text-text-secondary mb-1 uppercase tracking-wide">
          Docker
        </p>
        <div className="flex items-center justify-between rounded border border-border bg-surface dark:bg-surface px-2.5 py-1.5">
          <code className="text-[11px] font-mono text-text-primary dark:text-text-primary break-all">
            {docker}
          </code>
          <CopyButton text={docker} />
        </div>
      </div>
    </div>
  );
}

export default function Login() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showReset, setShowReset] = useState(false);

  useEffect(() => {
    if (status === 'needs-setup') navigate('/setup', { replace: true });
    if (status === 'authenticated') navigate('/dashboard', { replace: true });
  }, [status, navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Login failed. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-white dark:bg-bg px-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5 mb-8 justify-center">
          <div className="w-8 h-8 rounded-md bg-accent flex items-center justify-center">
            <ShieldCheck className="w-4.5 h-4.5 text-white" strokeWidth={2} />
          </div>
          <span className="text-[17px] font-medium text-text-primary dark:text-text-primary tracking-[-0.01em]">
            Vigilus
          </span>
        </div>

        <div className="border border-border rounded-lg p-6 bg-white dark:bg-bg">
          <h1 className="text-[15px] font-medium text-text-primary dark:text-text-primary mb-1">
            Sign in
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mb-5">
            Enter your credentials to continue.
          </p>

          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="block text-[12px] font-medium text-text-secondary dark:text-text-secondary mb-1">
                Username
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="input w-full"
                autoComplete="username"
                autoFocus
                required
              />
            </div>
            <div>
              <label className="block text-[12px] font-medium text-text-secondary dark:text-text-secondary mb-1">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input w-full"
                autoComplete="current-password"
                required
              />
            </div>

            {error && (
              <p className="text-[12px] text-error">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full mt-1"
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        <button
          type="button"
          onClick={() => setShowReset((v) => !v)}
          className="mt-3 flex items-center gap-1 text-[12px] text-text-secondary dark:text-text-secondary hover:text-text-primary dark:hover:text-text-primary transition-colors mx-auto"
        >
          Forgot password?
          <ChevronDown
            className={`w-3.5 h-3.5 transition-transform ${showReset ? 'rotate-180' : ''}`}
          />
        </button>

        {showReset && <ResetPanel />}
      </div>
    </div>
  );
}
