import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '@/lib/api';
import type { AuthUser } from '@/types';

type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated' | 'needs-setup';

interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  setup: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');
  const navigate = useNavigate();

  const checkAuth = useCallback(async () => {
    try {
      const me = await api.getMe();
      setUser(me);
      setStatus('authenticated');
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        try {
          const { needs_setup } = await api.getAuthSetupStatus();
          setStatus(needs_setup ? 'needs-setup' : 'unauthenticated');
        } catch {
          setStatus('unauthenticated');
        }
      } else {
        setStatus('unauthenticated');
      }
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    const handler = () => {
      setUser(null);
      setStatus('unauthenticated');
    };
    window.addEventListener('vigilus:unauthorized', handler);
    return () => window.removeEventListener('vigilus:unauthorized', handler);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const me = await api.login({ username, password });
    setUser(me);
    setStatus('authenticated');
    navigate('/dashboard');
  }, [navigate]);

  const setup = useCallback(async (username: string, password: string) => {
    const me = await api.setupFirstUser({ username, password });
    setUser(me);
    setStatus('authenticated');
    navigate('/dashboard');
  }, [navigate]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // ignore — clear state regardless
    }
    setUser(null);
    setStatus('unauthenticated');
    navigate('/login');
  }, [navigate]);

  return (
    <AuthContext.Provider value={{ user, status, login, setup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
