import React, { useState, useEffect } from 'react';
import { Activity, Download, ChevronDown, ChevronRight, Clock, CheckCircle2, XCircle, AlertCircle } from 'lucide-react';
import { api } from '@/lib/api';
import { Action, ActionOutcome } from '@/types';
import { cn } from '@/lib/utils';

export default function Actions() {
  const [actions, setActions] = useState<Action[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [outcomeFilter, setOutcomeFilter] = useState<string>('all');

  const fetchActions = async () => {
    try {
      setLoading(true);
      const data = await api.listActions();
      setActions(data);
    } catch (err) {
      console.error('Failed to fetch actions', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchActions();
  }, []);

  const handleExport = () => {
    window.open('/api/actions/export', '_blank');
  };

  const toggleRow = (id: string) => {
    setExpandedId(expandedId === id ? null : id);
  };

  const getOutcomeIcon = (outcome: ActionOutcome) => {
    switch (outcome) {
      case 'success': return <CheckCircle2 className="w-3.5 h-3.5 mr-1" />;
      case 'error': return <XCircle className="w-3.5 h-3.5 mr-1" />;
      case 'pending': return <Clock className="w-3.5 h-3.5 mr-1" />;
      case 'denied': return <AlertCircle className="w-3.5 h-3.5 mr-1" />;
      default: return null;
    }
  };

  const getOutcomeColor = (outcome: ActionOutcome) => {
    switch (outcome) {
      case 'success': return 'text-emerald-600 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-500/10 border-emerald-200 dark:border-emerald-500/20';
      case 'error': return 'text-red-600 bg-red-50 dark:text-red-400 dark:bg-red-500/10 border-red-200 dark:border-red-500/20';
      case 'pending': return 'text-amber-600 bg-amber-50 dark:text-amber-400 dark:bg-amber-500/10 border-amber-200 dark:border-amber-500/20';
      case 'denied': return 'text-gray-600 bg-gray-50 dark:text-gray-400 dark:bg-gray-500/10 border-gray-200 dark:border-gray-500/20';
      default: return 'text-gray-600 bg-gray-50 border-gray-200';
    }
  };

  const formatDate = (isoStr: string) => {
    const date = new Date(isoStr);
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(date);
  };

  const filteredActions = actions.filter(a => {
    if (outcomeFilter !== 'all' && a.outcome !== outcomeFilter) return false;
    return true;
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[20px] font-medium text-text-primary dark:text-text-primary tracking-[-0.02em]">
            Audit Log
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mt-1">
            Review history of tool executions, network access, and operator actions.
          </p>
        </div>
          <div className="flex items-center space-x-3">
            <select
              value={outcomeFilter}
              onChange={e => setOutcomeFilter(e.target.value)}
              className="px-3 py-1.5 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:outline-none focus:border-accent transition-colors"
            >
              <option value="all">All Outcomes</option>
              <option value="success">Success</option>
              <option value="error">Error</option>
              <option value="pending">Pending</option>
              <option value="denied">Denied</option>
            </select>
            <button
              onClick={handleExport}
              className="flex items-center px-3 py-1.5 text-[13px] font-medium rounded-md border border-border dark:border-border text-text-primary hover:bg-surface dark:hover:bg-border transition-colors"
            >
              <Download className="w-4 h-4 mr-1.5" />
              Export CSV
            </button>
          </div>
        </div>

      <div className="border border-border rounded-card bg-white dark:bg-surface dark:border-border overflow-hidden">
        {loading ? (
          <div className="py-20 text-center text-sm text-text-secondary">Loading audit logs...</div>
        ) : actions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 px-6">
            <div className="w-12 h-12 rounded-full bg-surface dark:bg-surface flex items-center justify-center mb-4">
              <Activity className="w-6 h-6 text-text-secondary/30 dark:text-text-secondary/30" strokeWidth={1.5} />
            </div>
            <p className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-1">
              No actions recorded
            </p>
            <p className="text-[13px] text-text-secondary dark:text-text-secondary text-center max-w-[320px]">
              Audit logs will appear here once operators begin executing tools and making requests.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border dark:border-border bg-surface/50 dark:bg-surface/50">
                  <th className="py-3 px-4 w-10"></th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Time</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Actor</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Tool</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Server</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Duration</th>
                  <th className="py-3 px-4 text-[12px] font-medium text-text-secondary uppercase tracking-wider">Outcome</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border dark:divide-border">
                {filteredActions.map((action) => (
                  <React.Fragment key={action.id}>
                    <tr 
                      onClick={() => toggleRow(action.id)}
                      className="hover:bg-surface/30 dark:hover:bg-border/30 transition-colors cursor-pointer"
                    >
                      <td className="py-3 px-4 text-text-secondary">
                        {expandedId === action.id ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                      </td>
                      <td className="py-3 px-4 text-[13px] text-text-primary dark:text-text-primary">
                        {formatDate(action.created_at)}
                      </td>
                      <td className="py-3 px-4 text-[13px] text-text-primary dark:text-text-primary">
                        {action.actor}
                      </td>
                      <td className="py-3 px-4 text-[13px] text-text-primary dark:text-text-primary font-medium">
                        {action.tool_name || <span className="text-text-secondary italic">Unknown</span>}
                      </td>
                      <td className="py-3 px-4 text-[13px] text-text-secondary">
                        {action.server_id || '-'}
                      </td>
                      <td className="py-3 px-4 text-[13px] text-text-secondary">
                        {action.duration_ms ? `${Math.round(action.duration_ms)}ms` : '-'}
                      </td>
                      <td className="py-3 px-4">
                        <div className={cn(
                          "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border",
                          getOutcomeColor(action.outcome)
                        )}>
                          {getOutcomeIcon(action.outcome)}
                          <span className="capitalize">{action.outcome}</span>
                        </div>
                      </td>
                    </tr>
                    {expandedId === action.id && (
                      <tr className="bg-surface/10 dark:bg-surface/10">
                        <td colSpan={7} className="p-4 border-t border-border dark:border-border">
                          <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                              <h4 className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Arguments</h4>
                              <pre className="bg-white dark:bg-surface border border-border dark:border-border rounded-md p-3 text-[12px] overflow-x-auto font-mono text-text-primary dark:text-text-primary">
                                {action.args ? JSON.stringify(action.args, null, 2) : 'No arguments'}
                              </pre>
                            </div>
                            <div className="space-y-2">
                              <h4 className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Error / Details</h4>
                              <pre className={cn(
                                "border rounded-md p-3 text-[12px] overflow-x-auto font-mono whitespace-pre-wrap",
                                action.error 
                                  ? "bg-red-50 dark:bg-red-500/10 border-red-200 dark:border-red-500/20 text-red-700 dark:text-red-400"
                                  : "bg-white dark:bg-surface border-border dark:border-border text-text-primary dark:text-text-primary"
                              )}>
                                {action.error || 'No error details recorded.'}
                              </pre>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
