import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { ApiError } from '@/lib/api';

export default function Setup() {
  const { status, setup } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (status === 'unauthenticated') navigate('/login', { replace: true });
    if (status === 'authenticated') navigate('/dashboard', { replace: true });
  }, [status, navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (password.length < 10) {
      setError('Password must be at least 10 characters.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setLoading(true);
    try {
      await setup(username, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Setup failed. Please try again.');
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
            Create admin account
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mb-5">
            No accounts exist yet. Create the first one to get started.
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
                minLength={3}
                maxLength={64}
              />
            </div>
            <div>
              <label className="block text-[12px] font-medium text-text-secondary dark:text-text-secondary mb-1">
                Password <span className="text-text-secondary/60">(min 10 chars)</span>
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input w-full"
                autoComplete="new-password"
                required
                minLength={10}
              />
            </div>
            <div>
              <label className="block text-[12px] font-medium text-text-secondary dark:text-text-secondary mb-1">
                Confirm password
              </label>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="input w-full"
                autoComplete="new-password"
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
              {loading ? 'Creating account…' : 'Create account'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
