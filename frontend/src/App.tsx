import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout } from './components/Layout';
import { NotificationsProvider } from './components/Notifications';
import { AuthProvider, useAuth } from './lib/auth';
import Dashboard from './pages/Dashboard';
import Operators from './pages/Operators';
import Chat from './pages/Chat';
import McpServers from './pages/McpServers';
import Jit from './pages/Jit';
import Tools from './pages/Tools';
import Tasks from './pages/Tasks';
import Servers from './pages/Servers';
import Scope from './pages/Scope';
import Actions from './pages/Actions';
import Settings from './pages/Settings';
import Login from './pages/Login';
import Setup from './pages/Setup';

const queryClient = new QueryClient();

function FullScreenSpinner() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-white dark:bg-bg">
      <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const { status } = useAuth();
  if (status === 'loading') return <FullScreenSpinner />;
  if (status === 'needs-setup') return <Navigate to="/setup" replace />;
  if (status === 'unauthenticated') return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <NotificationsProvider>
        <BrowserRouter>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/setup" element={<Setup />} />
              <Route
                path="/"
                element={
                  <RequireAuth>
                    <Layout />
                  </RequireAuth>
                }
              >
                <Route index element={<Navigate to="/dashboard" replace />} />
                <Route path="dashboard" element={<Dashboard />} />
                <Route path="operators" element={<Operators />} />
                <Route path="chat" element={<Chat />} />
                <Route path="mcp-servers" element={<McpServers />} />
                <Route path="jit" element={<Jit />} />
                <Route path="tools" element={<Tools />} />
                <Route path="tasks" element={<Tasks />} />
                <Route path="servers" element={<Servers />} />
                <Route path="scope" element={<Scope />} />
                <Route path="actions" element={<Actions />} />
                <Route path="settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Route>
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </NotificationsProvider>
    </QueryClientProvider>
  );
}
