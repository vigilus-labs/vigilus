import { useEffect, useRef, useState } from 'react';
import { Server as ServerIcon, Plus, Trash2, Edit2, ChevronDown, Radar } from 'lucide-react';
import { api } from '@/lib/api';
import { useToast, useConfirm } from '@/components/Notifications';
import { Server, Credential, ScopeInventoryHost } from '@/types';
import { cn } from '@/lib/utils';
import { PickFromScopeModal } from './PickFromScopeModal';

export default function Servers() {
  const toast = useToast();
  const confirm = useConfirm();
  const [servers, setServers] = useState<Server[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  // null = form closed, '' = adding, otherwise the id of the server being edited
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [fromScope, setFromScope] = useState(false);
  const addMenuRef = useRef<HTMLDivElement>(null);

  // Form State
  const [name, setName] = useState('');
  const [hostname, setHostname] = useState('');
  const [port, setPort] = useState('22');
  const [credentialId, setCredentialId] = useState('');
  const [os, setOs] = useState('');
  const [osVersion, setOsVersion] = useState('');
  const [ip, setIp] = useState('');

  // Close the "Add Server" chooser on an outside click (mirrors the
  // OpenRouter model-search dropdown in Settings).
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (addMenuRef.current && !addMenuRef.current.contains(e.target as Node)) {
        setAddMenuOpen(false);
      }
    };
    if (addMenuOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [addMenuOpen]);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [srvData, credData] = await Promise.all([
        api.listServers(),
        api.listCredentials()
      ]);
      setServers(srvData);
      setCredentials(credData);
    } catch (err) {
      console.error('Failed to fetch servers', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const openAdd = () => {
    setName('');
    setHostname('');
    setPort('22');
    setCredentialId('');
    setOs('');
    setOsVersion('');
    setIp('');
    setFromScope(false);
    setEditingId('');
  };

  const openEdit = (s: Server) => {
    setName(s.name);
    setHostname(s.hostname);
    setPort(String(s.port));
    setCredentialId(s.credential_id || '');
    setOs(s.os || '');
    setOsVersion(s.os_version || '');
    setIp(s.ip || '');
    setFromScope(false);
    setEditingId(s.id);
  };

  const closeForm = () => {
    setEditingId(null);
    setName('');
    setHostname('');
    setPort('22');
    setCredentialId('');
    setOs('');
    setOsVersion('');
    setIp('');
    setFromScope(false);
  };

  // "From Scope" picker handed back a discovered host — seed the normal add
  // form with it (still editable/confirmable) instead of creating silently.
  const pickFromScope = (host: ScopeInventoryHost) => {
    setPickerOpen(false);
    setName(host.hostname || host.ip);
    setHostname(host.hostname || host.ip);
    setPort('22');
    setCredentialId('');
    setOs(host.os || '');
    setOsVersion('');
    setIp(host.ip);
    setFromScope(true);
    setEditingId('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const payload = {
        name,
        hostname,
        port: parseInt(port),
        credential_id: credentialId || null,
        os: os.trim() || null,
        os_version: osVersion.trim() || null,
        ip: ip.trim() || null,
      };
      if (editingId === '') {
        await api.createServer(payload);
      } else {
        await api.updateServer(editingId!, payload);
      }
      toast(editingId === '' ? 'Server added' : 'Server updated', 'success');
      closeForm();
      fetchData();
    } catch (err: any) {
      toast(`Failed to ${editingId === '' ? 'add' : 'update'} server: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: 'Delete server?',
      message: 'This removes the server from Vigilus. The machine itself is not affected.',
    });
    if (!ok) return;
    try {
      await api.deleteServer(id);
      toast('Server deleted', 'success');
      fetchData();
    } catch (err: any) {
      toast(`Failed to delete server: ${err.message}`, 'error');
    }
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-medium text-text-primary mb-1">Servers</h1>
          <p className="text-text-secondary text-sm">Manage your homelab infrastructure and SSH credentials.</p>
        </div>
        <div className="relative" ref={addMenuRef}>
          <button
            onClick={() => (editingId === null ? setAddMenuOpen((v) => !v) : closeForm())}
            className="bg-accent hover:bg-accent-hover text-white px-4 py-2 rounded-md text-sm font-medium transition-colors flex items-center"
          >
            {editingId !== null ? (
              'Cancel'
            ) : (
              <>
                <Plus className="w-4 h-4 mr-2" />
                Add Server
                <ChevronDown className={cn('w-3.5 h-3.5 ml-1.5 transition-transform', addMenuOpen && 'rotate-180')} />
              </>
            )}
          </button>
          {addMenuOpen && editingId === null && (
            <div className="absolute right-0 mt-1 w-48 bg-white dark:bg-surface border border-border rounded-md shadow-lg z-20 overflow-hidden">
              <button
                onClick={() => { setAddMenuOpen(false); openAdd(); }}
                className="w-full text-left px-3 py-2.5 text-sm text-text-primary hover:bg-surface transition-colors"
              >
                From scratch
              </button>
              <button
                onClick={() => { setAddMenuOpen(false); setPickerOpen(true); }}
                className="w-full text-left px-3 py-2.5 text-sm text-text-primary hover:bg-surface transition-colors border-t border-border flex items-center gap-2"
              >
                <Radar className="w-3.5 h-3.5 text-text-secondary" />
                From Scope
              </button>
            </div>
          )}
        </div>
      </div>

      {pickerOpen && (
        <PickFromScopeModal onClose={() => setPickerOpen(false)} onPick={pickFromScope} />
      )}

      {editingId !== null && (
        <form onSubmit={handleSubmit} className="bg-surface border border-border rounded-card p-6 mb-8 max-w-2xl">
          <h3 className="text-sm font-medium text-text-primary mb-4 flex items-center gap-2">
            {editingId === '' ? 'Add New Server' : 'Edit Server'}
            {fromScope && (
              <span className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-info/15 text-info normal-case">
                <Radar className="w-2.5 h-2.5" /> from Scope
              </span>
            )}
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">Display Name</label>
              <input required value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Prod DB" className="input" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">Hostname / IP</label>
              <input required value={hostname} onChange={e => setHostname(e.target.value)} placeholder="192.168.1.100" className="input font-mono" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">SSH Port</label>
              <input required type="number" value={port} onChange={e => setPort(e.target.value)} className="input font-mono" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">Credential</label>
              <select value={credentialId} onChange={e => setCredentialId(e.target.value)} className="input">
                <option value="">None (Use local auth)</option>
                {credentials.map(c => (
                  <option key={c.id} value={c.id}>{c.name} ({c.type})</option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">
                IP Address <span className="text-text-secondary/50 normal-case">(optional — links to Scope)</span>
              </label>
              <input value={ip} onChange={e => setIp(e.target.value)} placeholder="192.168.1.100" className="input font-mono" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">OS Type <span className="text-text-secondary/50 normal-case">(optional)</span></label>
              <input value={os} onChange={e => setOs(e.target.value)} placeholder="e.g. Ubuntu" className="input" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[12px] font-medium text-text-secondary uppercase">OS Version <span className="text-text-secondary/50 normal-case">(optional)</span></label>
              <input value={osVersion} onChange={e => setOsVersion(e.target.value)} placeholder="e.g. 22.04" className="input font-mono" />
            </div>
          </div>
          <p className="text-[11px] text-text-secondary/70 mt-3">
            OS fields are optional — Vigilus auto-fills them from network scans when a host is matched, and shares them with operators for richer context.
          </p>
          <div className="flex justify-end gap-3 mt-6">
            <button type="button" onClick={closeForm} className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary">Cancel</button>
            <button type="submit" disabled={saving} className="px-4 py-2 text-sm font-medium bg-accent hover:bg-accent-hover text-white rounded-md disabled:opacity-50 transition-colors">
              {saving ? 'Saving...' : 'Save Server'}
            </button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="py-12 text-center text-text-secondary text-sm">Loading servers...</div>
      ) : servers.length === 0 ? (
        <div className="bg-surface border border-border rounded-card p-12 text-center">
          <div className="bg-white dark:bg-surface w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4 border border-border shadow-sm">
            <ServerIcon className="w-8 h-8 text-text-secondary" />
          </div>
          <h3 className="text-lg font-medium text-text-primary mb-2">No servers found</h3>
          <p className="text-text-secondary text-sm max-w-md mx-auto mb-6">
            Add your first server to allow Operators to execute commands, read files, and manage containers.
          </p>
          <button 
            onClick={openAdd}
            className="bg-white dark:bg-surface border border-border hover:bg-surface dark:hover:bg-border text-text-primary px-4 py-2 rounded-md text-sm font-medium transition-colors"
          >
            Add Server
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {servers.map(server => (
            <div key={server.id} className="bg-white dark:bg-surface border border-border rounded-card p-5 shadow-sm">
              <div className="flex justify-between items-start mb-4">
                <div className="flex items-center">
                  <div className={cn("w-2 h-2 rounded-full mr-2", server.status === 'online' ? "bg-success" : server.status === 'offline' ? "bg-danger" : "bg-warning")} />
                  <h3 className="font-medium text-text-primary">{server.name}</h3>
                </div>
                <div className="flex items-center space-x-1">
                  <button onClick={() => openEdit(server)} className="text-text-secondary hover:text-text-primary p-1">
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button onClick={() => handleDelete(server.id)} className="text-text-secondary hover:text-danger p-1">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-text-secondary">Host</span>
                  <span className="font-mono text-text-primary">{server.hostname}:{server.port}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-secondary">Auth</span>
                  <span className="text-text-primary">{server.credential_id ? 'Stored Credential' : 'Local / Key'}</span>
                </div>
                {(server.os || server.os_version) && (
                  <div className="flex justify-between">
                    <span className="text-text-secondary">OS</span>
                    <span className="text-text-primary">{[server.os, server.os_version].filter(Boolean).join(' ')}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
