import { useState, useEffect } from 'react';
import { ShieldCheck, Check, X, Clock, AlertCircle } from 'lucide-react';
import { api } from '@/lib/api';
import { JitRequest } from '@/types';
import { JitGrantControls, JitGrantOpts } from '@/components/JitGrantControls';

export default function Jit() {
  const [requests, setRequests] = useState<JitRequest[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchRequests();
    
    // Poll every 5s to keep it fresh
    const interval = setInterval(fetchRequests, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchRequests = async () => {
    try {
      const data = await api.listJitRequests();
      setRequests(data);
    } catch (err) {
      console.error('Failed to load JIT requests', err);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (id: string, opts?: JitGrantOpts) => {
    try {
      await api.approveJitRequest(id, opts);
      fetchRequests();
    } catch (err) {
      console.error('Failed to approve request', err);
    }
  };

  const handleDeny = async (id: string) => {
    try {
      await api.denyJitRequest(id);
      fetchRequests();
    } catch (err) {
      console.error('Failed to deny request', err);
    }
  };

  const pendingRequests = requests.filter(r => r.status === 'pending');
  const pastRequests = requests.filter(r => r.status !== 'pending');

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[20px] font-medium text-text-primary dark:text-text-primary tracking-[-0.02em]">
            Just-In-Time Access
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mt-1">
            Review and approve temporary elevated permissions for operators.
          </p>
        </div>
      </div>

      <div className="space-y-6">
        {/* Pending Section */}
        <div>
          <h2 className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-3 flex items-center">
            <Clock className="w-4 h-4 mr-1.5 text-warning" />
            Pending Approval ({pendingRequests.length})
          </h2>
          {pendingRequests.length === 0 ? (
            <div className="border border-border dark:border-border rounded-card bg-surface/30 dark:bg-surface/30 py-8 text-center">
              <ShieldCheck className="w-8 h-8 text-text-secondary/30 mx-auto mb-2" />
              <p className="text-[13px] text-text-secondary">No pending requests.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {pendingRequests.map(req => (
                <div key={req.id} className="border border-warning/30 bg-warning/5 rounded-card p-4 flex flex-col gap-3">
                  <div>
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="font-medium text-[14px] text-text-primary dark:text-text-primary">
                        {req.operator_name || req.operator_id}
                      </span>
                      <span className="text-[12px] text-text-secondary">requested</span>
                      <span className="px-1.5 py-0.5 rounded-sm bg-danger/10 text-danger text-[11px] font-bold uppercase tracking-wide">
                        {req.permission}
                      </span>
                      <span className="text-[12px] text-text-secondary">on</span>
                      <span className="font-mono text-[12px] text-text-primary dark:text-text-primary bg-surface dark:bg-surface px-1.5 py-0.5 rounded">
                        {req.resource}
                      </span>
                    </div>
                    <p className="text-[13px] text-text-secondary mb-2">
                      <strong className="font-medium text-text-primary dark:text-text-primary">Reason:</strong> {req.task_description}
                    </p>
                    <p className="text-[11px] text-text-secondary flex items-center">
                      <Clock className="w-3 h-3 mr-1" /> Requested {new Date(req.requested_at).toLocaleString()} • Expires in {req.ttl_minutes} mins after approval
                    </p>
                  </div>
                  <div className="border-t border-warning/20 pt-3">
                    <JitGrantControls
                      resource={req.resource}
                      onApprove={opts => handleApprove(req.id, opts)}
                      onDeny={() => handleDeny(req.id)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Past Section */}
        <div>
          <h2 className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-3">
            Request History
          </h2>
          <div className="border border-border dark:border-border rounded-card bg-white dark:bg-surface overflow-hidden">
            <table className="w-full text-left text-[13px]">
              <thead>
                <tr className="border-b border-border dark:border-border bg-surface/50 dark:bg-surface/50">
                  <th className="px-4 py-3 font-medium text-text-secondary">Time</th>
                  <th className="px-4 py-3 font-medium text-text-secondary">Operator</th>
                  <th className="px-4 py-3 font-medium text-text-secondary">Permission</th>
                  <th className="px-4 py-3 font-medium text-text-secondary">Resource</th>
                  <th className="px-4 py-3 font-medium text-text-secondary">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border dark:divide-border">
                {pastRequests.length === 0 && !loading ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-text-secondary">
                      No history found.
                    </td>
                  </tr>
                ) : (
                  pastRequests.map(req => (
                    <tr key={req.id} className="hover:bg-surface/30 dark:hover:bg-border/30 transition-colors">
                      <td className="px-4 py-3 text-text-secondary whitespace-nowrap">
                        {new Date(req.resolved_at || req.requested_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 font-medium text-text-primary dark:text-text-primary">
                        {req.operator_name || req.operator_id}
                      </td>
                      <td className="px-4 py-3">
                        <span className="px-1.5 py-0.5 rounded-sm bg-surface dark:bg-surface border border-border dark:border-border text-[11px] font-medium uppercase text-text-secondary">
                          {req.permission}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-[12px] truncate max-w-[200px]">
                        {req.resource}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`flex items-center text-[12px] font-medium ${
                          req.status === 'approved' ? 'text-success' :
                          req.status === 'denied' ? 'text-danger' : 'text-text-secondary'
                        }`}>
                          {req.status === 'approved' ? <Check className="w-3.5 h-3.5 mr-1" /> :
                           req.status === 'denied' ? <X className="w-3.5 h-3.5 mr-1" /> :
                           <AlertCircle className="w-3.5 h-3.5 mr-1" />}
                          <span className="capitalize">{req.status}</span>
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  );
}
