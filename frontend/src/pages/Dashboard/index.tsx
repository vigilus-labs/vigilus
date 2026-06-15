import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Activity, ShieldCheck, XCircle, Users, KeyRound, ArrowRight, Zap, Bot, Loader2, CircleDot } from 'lucide-react';
import { api } from '@/lib/api';
import { Action, Operator, RunningTask } from '@/types';
import { buildOperatorStatus, statusFor } from '@/lib/operatorStatus';

export default function Dashboard() {
  const [metrics, setMetrics] = useState<any>(null);
  const [actions, setActions] = useState<Action[]>([]);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [runningTasks, setRunningTasks] = useState<RunningTask[]>([]);
  const [loading, setLoading] = useState(true);

  const statusMap = buildOperatorStatus(runningTasks);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000); // 10s refresh
    return () => clearInterval(interval);
  }, []);

  // Faster poll just for live operator status.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const t = await api.listRunningTasks();
        if (!cancelled) setRunningTasks(t);
      } catch { /* transient */ }
    };
    tick();
    const iv = setInterval(tick, 3000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const fetchData = async () => {
    try {
      const [m, a, ops] = await Promise.all([
        api.getMetrics(),
        api.listActions({ limit: 8 }),
        api.listOperators(),
      ]);
      setMetrics(m);
      setActions(a.slice(0, 8)); // Just in case limit doesn't work perfectly
      setOperators(ops);
    } catch (err) {
      console.error('Failed to load dashboard data', err);
    } finally {
      setLoading(false);
    }
  };

  const getOutcomeBadge = (outcome: string) => {
    if (outcome === 'success') return <span className="px-1.5 py-0.5 rounded-sm bg-success/10 text-success text-[10px] uppercase font-bold tracking-wide">Success</span>;
    if (outcome === 'error') return <span className="px-1.5 py-0.5 rounded-sm bg-danger/10 text-danger text-[10px] uppercase font-bold tracking-wide">Error</span>;
    if (outcome === 'denied') return <span className="px-1.5 py-0.5 rounded-sm bg-danger/10 text-danger text-[10px] uppercase font-bold tracking-wide">Denied</span>;
    return <span className="px-1.5 py-0.5 rounded-sm bg-warning/10 text-warning text-[10px] uppercase font-bold tracking-wide">Pending</span>;
  };

  if (loading && !metrics) {
    return <div className="py-20 text-center text-[13px] text-text-secondary">Loading dashboard...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-[20px] font-medium text-text-primary dark:text-text-primary tracking-[-0.02em]">
            System Overview
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mt-1">
            Real-time operations, security events, and operator activity.
          </p>
        </div>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="p-5 border border-border dark:border-border rounded-card bg-white dark:bg-surface shadow-sm flex items-center justify-between group">
          <div>
            <p className="text-[12px] font-medium text-text-secondary uppercase tracking-wider mb-1">Active Operators</p>
            <h2 className="text-[28px] font-semibold text-text-primary dark:text-text-primary leading-none">
              {metrics?.active_operators || 0}
            </h2>
          </div>
          <div className="w-12 h-12 rounded-full bg-accent/10 flex items-center justify-center text-accent group-hover:scale-110 transition-transform">
            <Bot className="w-6 h-6" />
          </div>
        </div>

        <div className="p-5 border border-border dark:border-border rounded-card bg-white dark:bg-surface shadow-sm flex items-center justify-between group">
          <div>
            <p className="text-[12px] font-medium text-text-secondary uppercase tracking-wider mb-1">Pending JIT</p>
            <h2 className="text-[28px] font-semibold text-text-primary dark:text-text-primary leading-none">
              {metrics?.pending_jits || 0}
            </h2>
          </div>
          <div className="w-12 h-12 rounded-full bg-warning/10 flex items-center justify-center text-warning group-hover:scale-110 transition-transform">
            <KeyRound className="w-6 h-6" />
          </div>
        </div>

        <div className="p-5 border border-border dark:border-border rounded-card bg-white dark:bg-surface shadow-sm flex items-center justify-between group">
          <div>
            <p className="text-[12px] font-medium text-text-secondary uppercase tracking-wider mb-1">Failed Actions (24h)</p>
            <h2 className="text-[28px] font-semibold text-text-primary dark:text-text-primary leading-none">
              {metrics?.failed_actions_24h || 0}
            </h2>
          </div>
          <div className="w-12 h-12 rounded-full bg-danger/10 flex items-center justify-center text-danger group-hover:scale-110 transition-transform">
            <XCircle className="w-6 h-6" />
          </div>
        </div>

        <div className="p-5 border border-border dark:border-border rounded-card bg-white dark:bg-surface shadow-sm flex items-center justify-between group">
          <div>
            <p className="text-[12px] font-medium text-text-secondary uppercase tracking-wider mb-1">Total Actions</p>
            <h2 className="text-[28px] font-semibold text-text-primary dark:text-text-primary leading-none">
              {metrics?.total_actions || 0}
            </h2>
          </div>
          <div className="w-12 h-12 rounded-full bg-success/10 flex items-center justify-center text-success group-hover:scale-110 transition-transform">
            <Zap className="w-6 h-6" />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 pt-4">
        {/* Left Column: Recent Actions */}
        <div className="xl:col-span-2">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[15px] font-medium text-text-primary dark:text-text-primary flex items-center">
              <Activity className="w-4 h-4 mr-2 text-accent" />
              Recent Actions
            </h2>
            <Link to="/actions" className="text-[12px] text-text-secondary hover:text-accent font-medium flex items-center transition-colors">
              View all <ArrowRight className="w-3.5 h-3.5 ml-1" />
            </Link>
          </div>
          <div className="border border-border dark:border-border rounded-card bg-white dark:bg-surface overflow-hidden">
            <table className="w-full text-left text-[13px]">
              <thead>
                <tr className="border-b border-border dark:border-border bg-surface/50 dark:bg-surface/50">
                  <th className="px-4 py-3 font-medium text-text-secondary">Time</th>
                  <th className="px-4 py-3 font-medium text-text-secondary">Actor</th>
                  <th className="px-4 py-3 font-medium text-text-secondary">Tool</th>
                  <th className="px-4 py-3 font-medium text-text-secondary">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border dark:divide-border">
                {actions.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-text-secondary">No recent actions recorded.</td>
                  </tr>
                ) : (
                  actions.map(act => (
                    <tr key={act.id} className="hover:bg-surface/30 dark:hover:bg-border/30 transition-colors">
                      <td className="px-4 py-2.5 text-text-secondary whitespace-nowrap">
                        {new Date(act.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </td>
                      <td className="px-4 py-2.5 font-medium text-text-primary dark:text-text-primary">
                        {act.actor}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-[11px] truncate max-w-[200px] text-text-secondary">
                        {act.tool_name || act.event}
                      </td>
                      <td className="px-4 py-2.5">
                        {getOutcomeBadge(act.outcome)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right Column: Operator status + Quick Links */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[15px] font-medium text-text-primary dark:text-text-primary flex items-center">
              <Bot className="w-4 h-4 mr-2 text-accent" />
              Operators
            </h2>
            <Link to="/operators" className="text-[12px] text-text-secondary hover:text-accent font-medium flex items-center transition-colors">
              Manage <ArrowRight className="w-3.5 h-3.5 ml-1" />
            </Link>
          </div>
          <div className="border border-border dark:border-border rounded-card bg-white dark:bg-surface overflow-hidden mb-6">
            {operators.length === 0 ? (
              <div className="px-4 py-6 text-center text-[13px] text-text-secondary">No operators configured.</div>
            ) : (
              <div className="divide-y divide-border dark:divide-border max-h-[280px] overflow-y-auto">
                {[...operators]
                  .sort((a, b) => {
                    const ra = statusFor(statusMap, a.name).running ? 0 : 1;
                    const rb = statusFor(statusMap, b.name).running ? 0 : 1;
                    return ra - rb || a.name.localeCompare(b.name);
                  })
                  .map((op) => {
                    const st = statusFor(statusMap, op.name);
                    return (
                      <Link
                        key={op.id}
                        to="/operators"
                        className="flex items-center gap-2.5 px-4 py-2.5 hover:bg-surface/40 dark:hover:bg-border/30 transition-colors"
                      >
                        {st.running ? (
                          <Loader2 className="w-3.5 h-3.5 text-info animate-spin shrink-0" />
                        ) : (
                          <CircleDot className="w-3.5 h-3.5 text-text-secondary/40 shrink-0" />
                        )}
                        <span className="text-[13px] font-medium text-text-primary dark:text-text-primary truncate">
                          {op.name}
                        </span>
                        <span className="ml-auto text-[12px] text-text-secondary truncate max-w-[150px] text-right">
                          {st.running ? (st.currentStep || 'Running') : op.enabled ? 'Idle' : 'Disabled'}
                        </span>
                      </Link>
                    );
                  })}
              </div>
            )}
          </div>

          <h2 className="text-[15px] font-medium text-text-primary dark:text-text-primary mb-4 flex items-center">
            <Zap className="w-4 h-4 mr-2 text-warning" />
            Quick Actions
          </h2>
          <div className="space-y-3">
            <Link to="/chat" className="flex items-center p-4 border border-border dark:border-border rounded-card bg-white dark:bg-surface hover:border-accent/50 hover:bg-surface/50 dark:hover:bg-border/50 transition-all group">
              <div className="w-10 h-10 rounded-full bg-accent/10 flex items-center justify-center text-accent mr-4">
                <Bot className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <h3 className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-0.5 group-hover:text-accent transition-colors">Chat with Orchestrator</h3>
                <p className="text-[12px] text-text-secondary">Start a new conversation with the AI.</p>
              </div>
              <ArrowRight className="w-4 h-4 text-text-secondary group-hover:text-accent transition-colors" />
            </Link>

            <Link to="/operators" className="flex items-center p-4 border border-border dark:border-border rounded-card bg-white dark:bg-surface hover:border-accent/50 hover:bg-surface/50 dark:hover:bg-border/50 transition-all group">
              <div className="w-10 h-10 rounded-full bg-info/10 flex items-center justify-center text-info mr-4">
                <Users className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <h3 className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-0.5 group-hover:text-info transition-colors">Manage Operators</h3>
                <p className="text-[12px] text-text-secondary">Configure operator bounds and models.</p>
              </div>
              <ArrowRight className="w-4 h-4 text-text-secondary group-hover:text-info transition-colors" />
            </Link>

            <Link to="/jit" className="flex items-center p-4 border border-border dark:border-border rounded-card bg-white dark:bg-surface hover:border-accent/50 hover:bg-surface/50 dark:hover:bg-border/50 transition-all group">
              <div className="w-10 h-10 rounded-full bg-warning/10 flex items-center justify-center text-warning mr-4">
                <ShieldCheck className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <h3 className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-0.5 group-hover:text-warning transition-colors">Review Approvals</h3>
                <p className="text-[12px] text-text-secondary">Manage pending JIT elevation requests.</p>
              </div>
              <ArrowRight className="w-4 h-4 text-text-secondary group-hover:text-warning transition-colors" />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
