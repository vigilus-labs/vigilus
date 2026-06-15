import { useState, useEffect } from 'react';
import { Outlet, NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Bot,
  MessageSquare,
  Server,
  Wrench,
  HardDrive,
  ShieldCheck,
  KeyRound,
  Settings,
  Search,
  CalendarClock,
  Moon,
  Sun,
  Activity,
  Network,
  PanelLeftClose,
  PanelLeft,
  ChevronRight,
  LogOut,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { ActivityDrawer } from '@/components/ActivityDrawer';
import { useAuth } from '@/lib/auth';

interface NavItem {
  label: string;
  path: string;
  icon: React.ElementType;
}

const navItems: NavItem[] = [
  { label: 'Dashboard', path: '/dashboard', icon: LayoutDashboard },
  { label: 'Operators', path: '/operators', icon: Bot },
  { label: 'Vigilus Chat', path: '/chat', icon: MessageSquare },
  { label: 'Tasks', path: '/tasks', icon: CalendarClock },
  { label: 'MCP Servers', path: '/mcp-servers', icon: Server },
  { label: 'Tools', path: '/tools', icon: Wrench },
  { label: 'Servers', path: '/servers', icon: HardDrive },
  { label: 'Scope', path: '/scope', icon: Network },
  { label: 'Actions', path: '/actions', icon: ShieldCheck },
  { label: 'JIT', path: '/jit', icon: KeyRound },
  { label: 'Settings', path: '/settings', icon: Settings },
];

export function Layout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [isDark, setIsDark] = useState(() => {
    if (typeof window !== 'undefined') {
      return document.documentElement.classList.contains('dark');
    }
    return false;
  });
  const location = useLocation();
  const { user, logout } = useAuth();

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDark]);

  // Get current section label for breadcrumb
  const currentNav = navItems.find((item) =>
    location.pathname.startsWith(item.path),
  );

  // Routes that fill the content area edge-to-edge (no centered/padded column).
  const fullBleed = location.pathname.startsWith('/chat') || location.pathname.startsWith('/scope');

  return (
    <div className="flex h-screen overflow-hidden bg-white dark:bg-bg">
      {/* ── Sidebar ─────────────────────────────────────────────── */}
      <aside
        className={cn(
          'flex flex-col border-r border-border bg-white dark:bg-bg transition-[width] duration-200 ease-in-out shrink-0',
          sidebarCollapsed ? 'w-[60px]' : 'w-[240px]',
        )}
      >
        {/* Logo area */}
        <div className="flex items-center h-14 px-4 border-b border-border shrink-0">
          {!sidebarCollapsed && (
            <div className="flex items-center gap-2.5 min-w-0">
              <img src="/favicon.svg" alt="Vigilus" className="w-7 h-7 rounded-md shrink-0" />
              <span className="text-[15px] font-medium text-text-primary dark:text-text-primary tracking-[0.04em] truncate">
                VIGILUS
              </span>
            </div>
          )}
          {sidebarCollapsed && (
            <img src="/favicon.svg" alt="Vigilus" className="w-7 h-7 rounded-md mx-auto" />
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-3 px-2.5">
          <ul className="space-y-0.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.path}>
                  <NavLink
                    to={item.path}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-2.5 px-2.5 py-[7px] rounded-md text-[13px] transition-colors group relative',
                        isActive
                          ? 'bg-accent/[0.07] text-accent font-medium dark:bg-accent/20 dark:text-accent'
                          : 'text-text-secondary hover:text-text-primary hover:bg-surface dark:text-text-secondary dark:hover:text-text-primary dark:hover:bg-surface',
                        sidebarCollapsed && 'justify-center px-0',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {/* Active indicator bar */}
                        {isActive && (
                          <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-4 bg-accent rounded-r-full" />
                        )}
                        <Icon
                          className={cn(
                            'w-4 h-4 shrink-0',
                            isActive
                              ? 'text-accent dark:text-accent'
                              : 'text-text-secondary group-hover:text-text-primary dark:text-text-secondary dark:group-hover:text-text-primary',
                          )}
                          strokeWidth={1.75}
                        />
                        {!sidebarCollapsed && (
                          <span className="truncate">{item.label}</span>
                        )}
                      </>
                    )}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* Collapse toggle + user/logout */}
        <div className="border-t border-border px-2.5 py-2.5 shrink-0 space-y-0.5">
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className={cn(
              'flex items-center gap-2.5 px-2.5 py-[7px] rounded-md text-[13px] text-text-secondary hover:text-text-primary hover:bg-surface dark:text-text-secondary dark:hover:text-text-primary dark:hover:bg-surface transition-colors w-full',
              sidebarCollapsed && 'justify-center px-0',
            )}
          >
            {sidebarCollapsed ? (
              <PanelLeft className="w-4 h-4" strokeWidth={1.75} />
            ) : (
              <>
                <PanelLeftClose className="w-4 h-4" strokeWidth={1.75} />
                <span>Collapse</span>
              </>
            )}
          </button>
          <button
            onClick={() => logout()}
            title={user ? `Sign out ${user.username}` : 'Sign out'}
            className={cn(
              'flex items-center gap-2.5 px-2.5 py-[7px] rounded-md text-[13px] text-text-secondary hover:text-text-primary hover:bg-surface dark:text-text-secondary dark:hover:text-text-primary dark:hover:bg-surface transition-colors w-full',
              sidebarCollapsed && 'justify-center px-0',
            )}
          >
            <LogOut className="w-4 h-4 shrink-0" strokeWidth={1.75} />
            {!sidebarCollapsed && (
              <span className="truncate">{user?.username ?? 'Sign out'}</span>
            )}
          </button>
        </div>
      </aside>

      {/* ── Main content area ──────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="flex items-center justify-between h-14 px-6 border-b border-border bg-white dark:bg-bg shrink-0">
          {/* Left: Breadcrumb */}
          <div className="flex items-center gap-1.5 text-[13px]">
            <span className="text-text-secondary dark:text-text-secondary">Vigilus</span>
            {currentNav && (
              <>
                <ChevronRight className="w-3 h-3 text-text-secondary/50 dark:text-text-secondary/50" />
                <span className="text-text-primary dark:text-text-primary font-medium">
                  {currentNav.label}
                </span>
              </>
            )}
          </div>

          {/* Right: Actions */}
          <div className="flex items-center gap-1">
            {/* Search */}
            <div className="relative mr-2">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-secondary/60 dark:text-text-secondary/60" strokeWidth={1.75} />
              <input
                type="text"
                placeholder="Search…"
                className="input w-[200px] pl-8 py-1.5 text-[13px] h-8"
              />
            </div>

            {/* System status */}
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[12px] text-text-secondary dark:text-text-secondary">
              <div className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
              <span>Online</span>
            </div>

            {/* Dark mode toggle */}
            <button
              onClick={() => setIsDark(!isDark)}
              className="p-2 rounded-md text-text-secondary hover:text-text-primary hover:bg-surface dark:text-text-secondary dark:hover:text-text-primary dark:hover:bg-surface transition-colors"
              aria-label="Toggle dark mode"
            >
              {isDark ? (
                <Sun className="w-4 h-4" strokeWidth={1.75} />
              ) : (
                <Moon className="w-4 h-4" strokeWidth={1.75} />
              )}
            </button>

            {/* Activity drawer toggle */}
            <button
              onClick={() => setDrawerOpen(!drawerOpen)}
              className={cn(
                'p-2 rounded-md transition-colors',
                drawerOpen
                  ? 'text-accent bg-accent/[0.07] dark:bg-accent/20'
                  : 'text-text-secondary hover:text-text-primary hover:bg-surface dark:text-text-secondary dark:hover:text-text-primary dark:hover:bg-surface',
              )}
              aria-label="Toggle activity drawer"
            >
              <Activity className="w-4 h-4" strokeWidth={1.75} />
            </button>
          </div>
        </header>

        {/* Page content. Full-bleed routes (e.g. chat) fill the area edge-to-edge;
            everything else gets the centered, padded content column. */}
        <div className="flex-1 overflow-hidden flex">
          <main className={cn('flex-1 min-w-0', fullBleed ? 'overflow-hidden' : 'overflow-y-auto')}>
            {fullBleed ? (
              <Outlet />
            ) : (
              <div className="max-w-[1200px] mx-auto px-6 py-6">
                <Outlet />
              </div>
            )}
          </main>

          {/* Activity drawer */}
          <ActivityDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
        </div>
      </div>
    </div>
  );
}
