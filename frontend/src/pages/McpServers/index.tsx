import { useState, useEffect } from 'react';
import { Server, Plus, X, Play, Square, RefreshCw, Trash2, ClipboardPaste, Wrench, Loader2, Package, Terminal, Github, Pencil } from 'lucide-react';
import { api } from '@/lib/api';
import { useToast, useConfirm } from '@/components/Notifications';
import { McpServer, Operator } from '@/types';

// ── Install methods ──────────────────────────────────────────────────────
// The way the server's process is launched. Vigilus can only manage stdio
// servers it starts itself, so these are the three flows that actually work.
type InstallMethod = 'npm' | 'command' | 'github';

// Parse an "arguments" field that accepts either a JSON array
// (["-y", "pkg"]) or plain whitespace/newline-separated tokens (-y pkg).
function parseArgs(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith('[')) {
    try {
      const arr = JSON.parse(trimmed);
      if (Array.isArray(arr)) return arr.map(String);
    } catch { /* fall through to token split */ }
  }
  return trimmed.split(/\s+/);
}

// Parse a KEY=value-per-line block into an env object. Blank lines and
// lines starting with # are ignored.
function parseEnv(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const eq = t.indexOf('=');
    if (eq === -1) continue;
    env[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
  }
  return env;
}

function envToText(env: Record<string, unknown> | null | undefined): string {
  return Object.entries(env || {}).map(([k, v]) => `${k}=${v}`).join('\n');
}

export default function McpServers() {
  const toast = useToast();
  const confirm = useConfirm();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);

  // Import-from-JSON modal
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importing, setImporting] = useState(false);

  // Assign-tools modal
  const [assignServer, setAssignServer] = useState<McpServer | null>(null);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [selectedOperators, setSelectedOperators] = useState<string[]>([]);
  const [assigning, setAssigning] = useState(false);

  // Add / edit modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Shared form state
  const [method, setMethod] = useState<InstallMethod>('npm');
  const [name, setName] = useState('');
  const [envText, setEnvText] = useState('');
  const [autostart, setAutostart] = useState(false);

  // npm method
  const [packageName, setPackageName] = useState('');
  const [npmExtraArgs, setNpmExtraArgs] = useState('');

  // command method (also used as the "run" command for github)
  const [command, setCommand] = useState('');
  const [argsText, setArgsText] = useState('');

  // github method
  const [githubUrl, setGithubUrl] = useState('');
  const [installCommand, setInstallCommand] = useState('npm install');
  const [workingDir, setWorkingDir] = useState('');

  useEffect(() => {
    fetchServers();
    api.listOperators().then(setOperators).catch(() => {});
    const interval = setInterval(fetchServers, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleImport = async () => {
    if (!importText.trim()) return;
    setImporting(true);
    try {
      const result = await api.importMcpServers(importText);
      if (result.created.length > 0) {
        toast(`Imported ${result.created.length} server${result.created.length === 1 ? '' : 's'}: ${result.created.map(s => s.name).join(', ')}`, 'success');
      }
      if (result.skipped.length > 0) {
        toast(`Skipped (already exist): ${result.skipped.join(', ')}`, 'info');
      }
      for (const err of result.errors) {
        toast(err, 'error');
      }
      if (result.created.length > 0) {
        setIsImportOpen(false);
        setImportText('');
        fetchServers();
      }
    } catch (err: any) {
      toast(`Import failed: ${err.message ?? err}`, 'error');
    } finally {
      setImporting(false);
    }
  };

  const openAssign = (srv: McpServer) => {
    setAssignServer(srv);
    setSelectedOperators([]);
  };

  const handleAssign = async () => {
    if (!assignServer || selectedOperators.length === 0) return;
    setAssigning(true);
    try {
      const result = await api.assignMcpServerTools(assignServer.id, selectedOperators);
      toast(
        `Assigned ${result.tools} tool${result.tools === 1 ? '' : 's'} to ${result.operators} operator${result.operators === 1 ? '' : 's'} (${result.assigned} new)`,
        'success',
      );
      setAssignServer(null);
    } catch (err: any) {
      toast(`Assignment failed: ${err.message ?? err}`, 'error');
    } finally {
      setAssigning(false);
    }
  };

  const fetchServers = async () => {
    try {
      const data = await api.listMcpServers();
      setServers(data);
    } catch (err) {
      console.error('Failed to load MCP servers', err);
    } finally {
      setLoading(false);
    }
  };

  const resetForm = () => {
    setMethod('npm');
    setName('');
    setEnvText('');
    setAutostart(false);
    setPackageName('');
    setNpmExtraArgs('');
    setCommand('');
    setArgsText('');
    setGithubUrl('');
    setInstallCommand('npm install && npm run build');
    setWorkingDir('');
  };

  const openCreate = () => {
    setEditingId(null);
    resetForm();
    setIsModalOpen(true);
  };

  const openEdit = (srv: McpServer) => {
    setEditingId(srv.id);
    resetForm();
    setName(srv.name);
    setEnvText(envToText(srv.env_vars));
    setAutostart(srv.autostart);

    if (srv.github_url) {
      setMethod('github');
      setGithubUrl(srv.github_url);
      setInstallCommand(srv.install_command ?? '');
      setWorkingDir(srv.working_dir ?? '');
      setCommand(srv.command);
      setArgsText((srv.args ?? []).join(' '));
    } else if (srv.command === 'npx') {
      // Recover "npx -y <pkg> [extra...]" back into the npm fields.
      setMethod('npm');
      const a = srv.args ?? [];
      const pkgIdx = a.findIndex(x => !x.startsWith('-'));
      setPackageName(pkgIdx >= 0 ? a[pkgIdx] : '');
      setNpmExtraArgs(pkgIdx >= 0 ? a.slice(pkgIdx + 1).join(' ') : '');
    } else {
      setMethod('command');
      setCommand(srv.command);
      setArgsText((srv.args ?? []).join(' '));
    }
    setIsModalOpen(true);
  };

  const buildPayload = () => {
    const env_vars = parseEnv(envText);
    if (method === 'npm') {
      const pkg = packageName.trim();
      return {
        name,
        command: 'npx',
        args: ['-y', pkg, ...parseArgs(npmExtraArgs)],
        env_vars,
        transport: 'stdio' as const,
        autostart,
      };
    }
    if (method === 'github') {
      return {
        name,
        command: command.trim() || 'node',
        args: parseArgs(argsText),
        env_vars,
        transport: 'stdio' as const,
        autostart,
        github_url: githubUrl.trim(),
        install_command: installCommand.trim() || undefined,
        working_dir: workingDir.trim() || undefined,
      };
    }
    // command
    return {
      name,
      command: command.trim(),
      args: parseArgs(argsText),
      env_vars,
      transport: 'stdio' as const,
      autostart,
    };
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    // Lightweight per-method validation with clear messages.
    if (!name.trim()) return toast('Give the server a name.', 'error');
    if (method === 'npm' && !packageName.trim()) return toast('Enter the npm package name.', 'error');
    if (method === 'command' && !command.trim()) return toast('Enter the command to run.', 'error');
    if (method === 'github' && !githubUrl.trim()) return toast('Enter the GitHub repository URL.', 'error');

    setSaving(true);
    try {
      const payload = buildPayload();
      if (editingId) {
        await api.updateMcpServer(editingId, payload);
        toast('MCP server updated', 'success');
      } else {
        await api.createMcpServer(payload);
        toast('MCP server added', 'success');
      }
      setIsModalOpen(false);
      setEditingId(null);
      resetForm();
      fetchServers();
    } catch (err: any) {
      toast(`Error saving server: ${err.message ?? err}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: 'Delete MCP server?',
      message: 'Tools provided by this server will no longer be available to operators.',
    });
    if (!ok) return;
    try {
      await api.deleteMcpServer(id);
      toast('MCP server deleted', 'success');
      fetchServers();
    } catch (err: any) {
      console.error('Failed to delete server', err);
      toast(`Failed to delete server: ${err.message ?? err}`, 'error');
    }
  };

  const handleStart = async (id: string) => {
    try {
      await api.startMcpServer(id);
      fetchServers();
    } catch (err: any) {
      console.error('Failed to start server', err);
      toast(`Failed to start server: ${err.message ?? err}`, 'error');
    }
  };

  const handleStop = async (id: string) => {
    try {
      await api.stopMcpServer(id);
      fetchServers();
    } catch (err: any) {
      console.error('Failed to stop server', err);
      toast(`Failed to stop server: ${err.message ?? err}`, 'error');
    }
  };

  const handleRestart = async (id: string) => {
    try {
      await api.stopMcpServer(id);
      await api.startMcpServer(id);
      fetchServers();
    } catch (err: any) {
      console.error('Failed to restart server', err);
      toast(`Failed to restart server: ${err.message ?? err}`, 'error');
    }
  };

  const handleReinstall = async (srv: McpServer) => {
    const ok = await confirm({
      title: `Reinstall "${srv.name}"?`,
      message: 'The cloned repository will be deleted, then cloned and installed again from scratch.',
    });
    if (!ok) return;
    try {
      await api.reinstallMcpServer(srv.id);
      toast('Reinstall started — a fresh clone and install is running.', 'success');
      fetchServers();
    } catch (err: any) {
      console.error('Failed to reinstall server', err);
      toast(`Failed to reinstall server: ${err.message ?? err}`, 'error');
    }
  };

  // How the server is set up, mirroring the add-form's method tabs.
  const serverMethod = (srv: McpServer): { label: string; icon: React.ElementType } => {
    if (srv.github_url) return { label: 'GitHub', icon: Github };
    if (srv.command === 'npx') return { label: 'npm', icon: Package };
    return { label: 'command', icon: Terminal };
  };

  // Live preview of the command Vigilus will actually run.
  const previewCommand = (() => {
    if (method === 'npm') {
      const pkg = packageName.trim() || '<package>';
      const extra = parseArgs(npmExtraArgs);
      return `npx ${['-y', pkg, ...extra].join(' ')}`;
    }
    if (method === 'github') {
      return `${command.trim() || 'node'} ${parseArgs(argsText).join(' ')}`.trim();
    }
    return `${command.trim() || '<command>'} ${parseArgs(argsText).join(' ')}`.trim();
  })();

  const methodTabs: { id: InstallMethod; label: string; icon: React.ElementType }[] = [
    { id: 'npm', label: 'npm package', icon: Package },
    { id: 'command', label: 'Custom command', icon: Terminal },
    { id: 'github', label: 'GitHub repo', icon: Github },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[20px] font-medium text-text-primary dark:text-text-primary tracking-[-0.02em]">
            MCP Servers
          </h1>
          <p className="text-[13px] text-text-secondary dark:text-text-secondary mt-1">
            Manage external Model Context Protocol servers to dynamically supply tools.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsImportOpen(true)}
            className="flex items-center px-3 py-1.5 text-[13px] font-medium rounded-md border border-border dark:border-border text-text-primary dark:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors"
          >
            <ClipboardPaste className="w-4 h-4 mr-1.5" />
            Import JSON
          </button>
          <button
            onClick={openCreate}
            className="flex items-center px-3 py-1.5 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover transition-colors shadow-sm"
          >
            <Plus className="w-4 h-4 mr-1.5" />
            Add Server
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
        {loading ? (
          <div className="col-span-full py-20 text-center text-[13px] text-text-secondary">Loading MCP Servers...</div>
        ) : servers.length === 0 ? (
          <div className="col-span-full flex flex-col items-center justify-center py-20 px-6 border border-border dark:border-border rounded-card bg-white dark:bg-surface">
            <div className="w-12 h-12 rounded-full bg-surface dark:bg-surface flex items-center justify-center mb-4">
              <Server className="w-6 h-6 text-text-secondary/30 dark:text-text-secondary/30" strokeWidth={1.5} />
            </div>
            <p className="text-[14px] font-medium text-text-primary dark:text-text-primary mb-1">
              No MCP Servers Configured
            </p>
            <p className="text-[13px] text-text-secondary dark:text-text-secondary text-center max-w-[320px]">
              Add an MCP server (e.g. the npm package <code className="font-mono text-[12px]">@modelcontextprotocol/server-everything</code>) to expand Vigilus capabilities dynamically.
            </p>
          </div>
        ) : (
          servers.map(srv => (
            <div key={srv.id} className="border border-border dark:border-border rounded-card bg-white dark:bg-surface flex flex-col overflow-hidden transition-shadow hover:shadow-sm">
              <div className="p-5 flex-1">
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-surface dark:bg-surface border border-border dark:border-border flex items-center justify-center">
                      <Server className="w-5 h-5 text-text-secondary" />
                    </div>
                    <div>
                      <h3 className="text-[15px] font-medium text-text-primary dark:text-text-primary">{srv.name}</h3>
                      <div className="flex items-center gap-2 mt-1">
                        <div className="flex items-center">
                          <div className={`w-1.5 h-1.5 rounded-full mr-1.5 ${
                            srv.status === 'running' ? 'bg-success' :
                            srv.status === 'error' ? 'bg-danger' :
                            'bg-text-secondary/50'
                          }`} />
                          <span className="text-[12px] text-text-secondary capitalize">{srv.status}</span>
                        </div>
                        {(() => {
                          const m = serverMethod(srv);
                          const MIcon = m.icon;
                          return (
                            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded border border-border dark:border-border text-[10px] font-medium text-text-secondary uppercase tracking-wider">
                              <MIcon className="w-3 h-3" /> {m.label}
                            </span>
                          );
                        })()}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="space-y-3 mb-4">
                  <div>
                    <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider block mb-1">Command</span>
                    <div className="px-2 py-1.5 bg-surface dark:bg-surface border border-border dark:border-border rounded font-mono text-[12px] text-text-primary dark:text-text-primary truncate">
                      {srv.command} {srv.args?.join(' ')}
                    </div>
                  </div>
                  {srv.last_error && (
                    <div className="px-3 py-2 bg-danger/10 border border-danger/20 rounded text-[12px] text-danger max-h-[100px] overflow-y-auto">
                      <strong>Error:</strong> {srv.last_error}
                    </div>
                  )}
                </div>
              </div>
              <div className="px-5 py-3 border-t border-border dark:border-border bg-surface/30 dark:bg-surface/30 flex justify-between gap-2">
                <div className="flex gap-2">
                  {srv.status === 'running' ? (
                    <button onClick={() => handleStop(srv.id)} className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-danger hover:bg-danger/10 transition-colors flex items-center">
                      <Square className="w-3.5 h-3.5 mr-1" /> Stop
                    </button>
                  ) : (
                    <button onClick={() => handleStart(srv.id)} className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-success hover:bg-success/10 transition-colors flex items-center">
                      <Play className="w-3.5 h-3.5 mr-1" /> Start
                    </button>
                  )}
                  {srv.status === 'running' && (
                    <button onClick={() => handleRestart(srv.id)} className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors flex items-center">
                      <RefreshCw className="w-3.5 h-3.5 mr-1" /> Restart
                    </button>
                  )}
                  {srv.github_url && (
                    <button
                      onClick={() => handleReinstall(srv)}
                      className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors flex items-center"
                      title="Delete the cloned repo and re-run clone + install"
                    >
                      <Package className="w-3.5 h-3.5 mr-1" /> Reinstall
                    </button>
                  )}
                  <button
                    onClick={() => openAssign(srv)}
                    className="px-3 py-1.5 text-[12px] font-medium rounded text-text-secondary hover:text-accent hover:bg-accent/10 transition-colors flex items-center"
                    title="Assign this server's tools to operators"
                  >
                    <Wrench className="w-3.5 h-3.5 mr-1" /> Assign Tools
                  </button>
                </div>
                <div className="flex gap-1">
                  <button onClick={() => openEdit(srv)} className="p-1.5 rounded text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors" title="Edit">
                    <Pencil className="w-4 h-4" />
                  </button>
                  <button onClick={() => handleDelete(srv.id)} className="p-1.5 rounded text-text-secondary hover:text-danger hover:bg-danger/10 transition-colors" title="Delete">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Import JSON modal */}
      {isImportOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-surface border border-border dark:border-border rounded-card w-full max-w-xl shadow-xl flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-border dark:border-border">
              <h2 className="text-[16px] font-medium text-text-primary dark:text-text-primary">Import MCP Servers</h2>
              <button onClick={() => setIsImportOpen(false)} className="text-text-secondary hover:text-text-primary"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-3">
              <p className="text-[13px] text-text-secondary dark:text-text-secondary">
                Paste the standard <code className="font-mono text-[12px] bg-surface dark:bg-surface px-1 py-0.5 rounded">mcpServers</code> JSON
                block from any MCP server's README (the same config used by Claude Desktop and Cursor).
              </p>
              <textarea
                value={importText}
                onChange={e => setImportText(e.target.value)}
                rows={12}
                spellCheck={false}
                placeholder={`{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
      "env": { "SOME_KEY": "value" }
    }
  }
}`}
                className="w-full px-3 py-2 text-[12px] font-mono bg-surface/50 dark:bg-surface/50 border border-border dark:border-border rounded-md focus:border-accent resize-y text-text-primary dark:text-text-primary"
              />
            </div>
            <div className="px-6 py-4 border-t border-border dark:border-border flex justify-end gap-3 bg-surface/30 dark:bg-surface/30">
              <button onClick={() => setIsImportOpen(false)} className="px-4 py-2 text-[13px] font-medium text-text-secondary hover:text-text-primary">Cancel</button>
              <button
                onClick={handleImport}
                disabled={importing || !importText.trim()}
                className="flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover disabled:opacity-50"
              >
                {importing && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Import
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Assign tools modal */}
      {assignServer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-surface border border-border dark:border-border rounded-card w-full max-w-md shadow-xl flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-border dark:border-border">
              <h2 className="text-[16px] font-medium text-text-primary dark:text-text-primary">
                Assign tools from "{assignServer.name}"
              </h2>
              <button onClick={() => setAssignServer(null)} className="text-text-secondary hover:text-text-primary"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-3">
              <p className="text-[13px] text-text-secondary dark:text-text-secondary">
                All tools discovered from this server will be assigned to the selected operators.
                {assignServer.status !== 'running' && (
                  <span className="block mt-1.5 text-warning">
                    ⚠ This server isn't running — if its tools haven't been discovered yet, start it first.
                  </span>
                )}
              </p>
              <div className="space-y-1 max-h-[260px] overflow-y-auto">
                {operators.filter(o => o.enabled).map(op => (
                  <label
                    key={op.id}
                    className="flex items-center gap-2.5 px-3 py-2 rounded-md hover:bg-surface dark:hover:bg-border cursor-pointer text-[13px] text-text-primary dark:text-text-primary"
                  >
                    <input
                      type="checkbox"
                      checked={selectedOperators.includes(op.id)}
                      onChange={e => setSelectedOperators(prev =>
                        e.target.checked ? [...prev, op.id] : prev.filter(id => id !== op.id),
                      )}
                      className="rounded border-border text-accent focus:ring-accent"
                    />
                    <span className="font-medium">{op.name}</span>
                    <span className="text-[11px] text-text-secondary ml-auto">{op.permission_level}</span>
                  </label>
                ))}
                {operators.filter(o => o.enabled).length === 0 && (
                  <p className="text-[13px] text-text-secondary px-3 py-2">No enabled operators.</p>
                )}
              </div>
            </div>
            <div className="px-6 py-4 border-t border-border dark:border-border flex justify-end gap-3 bg-surface/30 dark:bg-surface/30">
              <button onClick={() => setAssignServer(null)} className="px-4 py-2 text-[13px] font-medium text-text-secondary hover:text-text-primary">Cancel</button>
              <button
                onClick={handleAssign}
                disabled={assigning || selectedOperators.length === 0}
                className="flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover disabled:opacity-50"
              >
                {assigning && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Assign
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add / edit modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-surface border border-border dark:border-border rounded-card w-full max-w-lg shadow-xl flex flex-col max-h-[90vh]">
            <div className="flex items-center justify-between px-6 py-4 border-b border-border dark:border-border">
              <h2 className="text-[16px] font-medium text-text-primary dark:text-text-primary">
                {editingId ? 'Edit MCP Server' : 'Add MCP Server'}
              </h2>
              <button onClick={() => { setIsModalOpen(false); setEditingId(null); }} className="text-text-secondary hover:text-text-primary"><X className="w-5 h-5" /></button>
            </div>
            <form onSubmit={handleSave} className="flex flex-col overflow-hidden">
              <div className="p-6 space-y-4 overflow-y-auto">

                {/* Method selector */}
                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Install method</label>
                  <div className="grid grid-cols-3 gap-2">
                    {methodTabs.map(t => {
                      const Icon = t.icon;
                      const active = method === t.id;
                      return (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => setMethod(t.id)}
                          className={`flex flex-col items-center gap-1.5 px-2 py-3 rounded-md border text-[12px] font-medium transition-colors ${
                            active
                              ? 'border-accent bg-accent/5 text-accent'
                              : 'border-border dark:border-border text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border'
                          }`}
                        >
                          <Icon className="w-4 h-4" />
                          {t.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Name</label>
                  <input required value={name} onChange={e => setName(e.target.value)} placeholder="e.g. nmap" className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent" />
                </div>

                {/* npm method */}
                {method === 'npm' && (
                  <>
                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">npm package</label>
                      <input value={packageName} onChange={e => setPackageName(e.target.value)} placeholder="@ebowwa/mcp-nmap" className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent font-mono" />
                      <p className="text-[11px] text-text-secondary/70">
                        Just the package name from npm. Vigilus runs it with <code className="font-mono">npx -y</code> — no manual install or build needed.
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Extra arguments (optional)</label>
                      <input value={npmExtraArgs} onChange={e => setNpmExtraArgs(e.target.value)} placeholder="/data --read-only" className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent font-mono" />
                      <p className="text-[11px] text-text-secondary/70">Space-separated, or a JSON array. Passed to the package after its name.</p>
                    </div>
                  </>
                )}

                {/* github method */}
                {method === 'github' && (
                  <div className="p-4 rounded-md border border-accent/20 bg-accent/5 space-y-4">
                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-accent uppercase tracking-wider">Repository URL</label>
                      <input value={githubUrl} onChange={e => setGithubUrl(e.target.value)} placeholder="https://github.com/owner/repo" className="w-full px-3 py-2 text-[13px] bg-white/50 dark:bg-black/20 border border-accent/20 rounded-md focus:border-accent font-mono" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Subdirectory / Working dir (optional)</label>
                      <input value={workingDir} onChange={e => setWorkingDir(e.target.value)} placeholder="src/sqlite" className="w-full px-3 py-2 text-[13px] bg-white/50 dark:bg-black/20 border border-accent/20 rounded-md focus:border-accent font-mono" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Install command (optional)</label>
                      <input value={installCommand} onChange={e => setInstallCommand(e.target.value)} placeholder="npm install --omit=dev" className="w-full px-3 py-2 text-[13px] bg-white/50 dark:bg-black/20 border border-accent/20 rounded-md focus:border-accent font-mono" />
                      <p className="text-[11px] text-text-secondary/70">One executable with arguments. Shell operators such as <code className="font-mono">&&</code>, pipes, redirects, and substitutions are not supported.</p>
                    </div>
                  </div>
                )}

                {/* command + args for command & github run step */}
                {(method === 'command' || method === 'github') && (
                  <>
                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">{method === 'github' ? 'Run command' : 'Command'}</label>
                      <input value={command} onChange={e => setCommand(e.target.value)} placeholder={method === 'github' ? 'node' : 'npx'} className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent font-mono" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Arguments</label>
                      <input value={argsText} onChange={e => setArgsText(e.target.value)} placeholder={method === 'github' ? 'dist/index.js' : '-y @modelcontextprotocol/server-everything'} className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent font-mono" />
                      <p className="text-[11px] text-text-secondary/70">Space-separated, or a JSON array like <code className="font-mono">["-y", "pkg"]</code>.</p>
                    </div>
                  </>
                )}

                <div className="space-y-1.5">
                  <label className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">Environment variables (optional)</label>
                  <textarea value={envText} onChange={e => setEnvText(e.target.value)} rows={2} placeholder={'API_KEY=sk-...\nREGION=us-east-1'} className="w-full px-3 py-2 text-[13px] bg-transparent border border-border dark:border-border rounded-md focus:border-accent font-mono resize-y" />
                  <p className="text-[11px] text-text-secondary/70">One <code className="font-mono">KEY=value</code> per line.</p>
                </div>

                {/* Live command preview */}
                <div className="space-y-1.5">
                  <label className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">Vigilus will run</label>
                  <div className="px-3 py-2 bg-surface dark:bg-surface border border-border dark:border-border rounded font-mono text-[12px] text-text-primary dark:text-text-primary break-all">
                    {previewCommand}
                  </div>
                </div>

                <div className="flex items-center gap-2 pt-1">
                  <input type="checkbox" id="autostart" checked={autostart} onChange={e => setAutostart(e.target.checked)} className="rounded border-border text-accent focus:ring-accent" />
                  <label htmlFor="autostart" className="text-[13px] text-text-primary dark:text-text-primary">Start automatically on boot</label>
                </div>
              </div>
              <div className="px-6 py-4 border-t border-border dark:border-border flex justify-end gap-3 bg-surface/30 dark:bg-surface/30">
                <button type="button" onClick={() => { setIsModalOpen(false); setEditingId(null); }} className="px-4 py-2 text-[13px] font-medium text-text-secondary hover:text-text-primary">Cancel</button>
                <button type="submit" disabled={saving} className="flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium rounded-md bg-accent text-white hover:bg-accent-hover disabled:opacity-50">
                  {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {editingId ? 'Save Changes' : 'Add Server'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
