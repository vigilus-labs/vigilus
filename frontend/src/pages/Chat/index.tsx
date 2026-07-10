import { useState, useEffect, useRef, useCallback } from 'react';
import { Send, Bot, User, Wrench, MessageSquare, Trash2, Settings, Activity, Zap, Cpu, CheckCircle2, AlertCircle, KeyRound, Pencil, X, Brain, AtSign, Square, Loader2, ListChecks, Terminal, Share2, Search, Globe, CalendarClock } from 'lucide-react';
import { api } from '@/lib/api';
import { MemoryPanel } from '@/components/MemoryPanel';
import { JitGrantControls, JitGrantOpts } from '@/components/JitGrantControls';
import { Markdown } from '@/components/Markdown';
import { ProviderWizard } from '@/components/ProviderWizard';
import { ChatStream, ActivityEvent, SSEEventType, SSEEventData, nextActivityId } from '@/lib/sse';
import { useVigilusEvents } from '@/lib/ws';
import { Session, Message as Msg, Provider, Operator, RunningTask, CommandSpec, CommandResult } from '@/types';

interface JitChatItem {
  id: string;
  operator_name?: string;
  resource?: string;
  permission?: string;
  task_description?: string;
  resolution?: 'approved' | 'denied';
  resolving?: boolean;
}

interface OrchestratorConfig {
  provider_id: string | null;
  model: string | null;
  system_prompt: string;
  soul?: string | null;
}

// Human-readable labels for third-party chat platforms.
const EXTERNAL_LABELS: Record<string, string> = {
  telegram: 'Telegram',
  discord: 'Discord',
};

// A session is "external" when it originated from a third-party channel
// (Telegram, Discord, or any future integration).
const isExternalSession = (s: Session): boolean =>
  !!s.origin && s.origin !== 'web' && s.origin !== 'schedule';

// Scheduled-task runs get their own "Tasks" tab.
const isScheduledSession = (s: Session): boolean => s.origin === 'schedule';

type ChatTab = 'app' | 'channels' | 'tasks';

// Which sidebar tab a session belongs to. App = web/legacy chats (everything
// that isn't a channel or a scheduled run).
const matchesTab = (s: Session, tab: ChatTab): boolean =>
  tab === 'channels'
    ? isExternalSession(s)
    : tab === 'tasks'
      ? isScheduledSession(s)
      : !isExternalSession(s) && !isScheduledSession(s);

export default function Chat() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  // Which group of chats the sidebar shows: in-app chats vs. third-party channels.
  const [chatTab, setChatTab] = useState<ChatTab>('app');
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // @operator mention autocomplete
  const [operators, setOperators] = useState<Operator[]>([]);
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);

  // slash command autocomplete
  const [commands, setCommands] = useState<CommandSpec[]>([]);
  const [cmdPopup, setCmdPopup] = useState<{ start: number; query: string } | null>(null);
  const [cmdIndex, setCmdIndex] = useState(0);

  // ephemeral system notices injected by command results
  const [sysNotices, setSysNotices] = useState<Array<{ id: string; kind: 'markdown' | 'error'; text: string }>>([]);

  // provider wizard (/login)
  const [showWizard, setShowWizard] = useState(false);

  // Live (running) tasks — polled so we can view and cancel in-flight turns
  const [runningTasks, setRunningTasks] = useState<RunningTask[]>([]);
  const [showTasks, setShowTasks] = useState(false);
  const [cancelling, setCancelling] = useState<Record<string, boolean>>({});
  // Scrollable messages column + whether we should keep pinning to the bottom
  // as new content streams in. Set false when the user scrolls up, restored
  // when they scroll back down to the bottom.
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  // Activity feed for live streaming
  const [activities, setActivities] = useState<ActivityEvent[]>([]);
  const streamRef = useRef<ChatStream | null>(null);
  // Persistent show/hide for the actions panel (so it doesn't pop in and out
  // each turn). Preference is remembered across sessions.
  const [showActivity, setShowActivity] = useState(() => {
    if (typeof window === 'undefined') return true;
    return localStorage.getItem('vigilus.showActivity') !== 'false';
  });
  useEffect(() => {
    localStorage.setItem('vigilus.showActivity', String(showActivity));
  }, [showActivity]);

  // JIT approval requests raised during this conversation
  const [jitItems, setJitItems] = useState<JitChatItem[]>([]);

  // Approvals may be made from the app-wide banner or the JIT page rather
  // than the inline card. Reflect their globally broadcast resolution here
  // so the chat never presents stale, unusable controls.
  useVigilusEvents({
    events: {
      'jit.resolved': event => {
        const { id, status } = event.payload as { id?: string; status?: string };
        if (!id || (status !== 'approved' && status !== 'denied')) return;
        setJitItems(prev => prev.map(item => (
          item.id === id ? { ...item, resolving: false, resolution: status } : item
        )));
      },
    },
  });

  // Inline rename state for the session sidebar
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');

  // Orchestrator config state
  const [providers, setProviders] = useState<Provider[]>([]);
  const [orchConfig, setOrchConfig] = useState<OrchestratorConfig | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [configTab, setConfigTab] = useState<'provider' | 'soul'>('provider');
  const [selectedProviderId, setSelectedProviderId] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [soulDraft, setSoulDraft] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);

  useEffect(() => {
    fetchSessions();
    loadOrchestratorConfig();
    api.listProviders().then(setProviders).catch(() => {});
    api.listOperators().then(setOperators).catch(() => {});
    api.listCommands().then(setCommands).catch(() => {});
  }, []);

  // Poll for in-flight tasks so the live-tasks view and Stop button stay current.
  useEffect(() => {
    let active = true;
    const poll = () => api.listRunningTasks().then(t => { if (active) setRunningTasks(t); }).catch(() => {});
    poll();
    const id = setInterval(poll, 3000);
    return () => { active = false; clearInterval(id); };
  }, []);

  // Drop stale "cancelling" flags once a task is no longer running.
  useEffect(() => {
    setCancelling(prev => {
      const live = new Set(runningTasks.map(t => t.session_id));
      const next: Record<string, boolean> = {};
      for (const k of Object.keys(prev)) if (live.has(k)) next[k] = prev[k];
      return next;
    });
  }, [runningTasks]);

  const cancelTask = async (sessionId: string) => {
    setCancelling(prev => ({ ...prev, [sessionId]: true }));
    try {
      await api.cancelRunningTask(sessionId);
      api.listRunningTasks().then(setRunningTasks).catch(() => {});
    } catch (err) {
      console.error('Failed to cancel task', err);
      setCancelling(prev => ({ ...prev, [sessionId]: false }));
    }
  };

  // Is the active session's turn running server-side? (Even if it was started
  // before this component mounted — e.g. the user navigated away and back.)
  const activeRunning = activeSession
    ? runningTasks.find(t => t.session_id === activeSession.id) ?? null
    : null;
  // "Busy" = a turn is in progress, whether driven locally (SSE) or restored.
  const isBusy = loading || !!activeRunning;

  // Restore the live action feed for a turn we did NOT start locally (no SSE):
  // poll its buffered activity from the server so the actions reappear.
  useEffect(() => {
    if (!activeRunning || loading) return;
    let active = true;
    const sid = activeRunning.session_id;
    const fetchBuffer = () => api.getRunningTask(sid).then(res => {
      if (!active || !res.running) return;
      setActivities((res.activity || []).map(a => ({
        id: nextActivityId(),
        type: a.type as SSEEventType,
        data: a.data as SSEEventData,
        timestamp: Date.parse(a.ts) || Date.now(),
      })));
    }).catch(() => {});
    fetchBuffer();
    const id = setInterval(fetchBuffer, 2000);
    return () => { active = false; clearInterval(id); };
  }, [activeRunning?.session_id, loading]);

  // When a restored turn finishes, reload messages so the response appears.
  const prevRunningSidRef = useRef<string | null>(null);
  useEffect(() => {
    const cur = activeRunning?.session_id ?? null;
    const prev = prevRunningSidRef.current;
    prevRunningSidRef.current = cur;
    if (prev && !cur && !loading && activeSession && prev === activeSession.id) {
      api.listMessages(activeSession.id).then(setMessages).catch(() => {});
    }
  }, [activeRunning?.session_id, loading, activeSession?.id]);

  // Close the SSE stream if the page unmounts mid-turn (the server-side turn
  // keeps running; we re-attach via the buffer on return).
  useEffect(() => {
    return () => {
      if (streamRef.current) { streamRef.current.close(); streamRef.current = null; }
    };
  }, []);

  const loadOrchestratorConfig = async () => {
    try {
      const cfg = await api.getOrchestratorConfig();
      setOrchConfig(cfg);
      if (cfg.provider_id) setSelectedProviderId(cfg.provider_id);
      if (cfg.model) setSelectedModel(cfg.model);
      setSoulDraft(cfg.soul ?? '');
    } catch (err) {
      console.error('Failed to load orchestrator config', err);
    }
  };

  // Load models when provider changes
  useEffect(() => {
    if (!selectedProviderId) {
      setModels([]);
      return;
    }
    const prov = providers.find(p => p.id === selectedProviderId);
    if (!prov) return;

    setLoadingModels(true);
    api.testProvider(selectedProviderId).then(res => {
      if (res.ok && res.models) {
        setModels(res.models);
      } else {
        setModels(prov.default_model ? [prov.default_model] : []);
      }
    }).catch(() => {
      setModels(prov.default_model ? [prov.default_model] : []);
    }).finally(() => setLoadingModels(false));
  }, [selectedProviderId, providers]);

  const saveOrchConfig = async () => {
    try {
      const cfg = await api.updateOrchestratorConfig({
        provider_id: selectedProviderId || null,
        model: selectedModel || null,
        soul: soulDraft,
      });
      setOrchConfig(cfg);
      setShowConfig(false);
    } catch (err: any) {
      console.error('Failed to save orchestrator config', err);
    }
  };

  const currentProvider = providers.find(p => p.id === orchConfig?.provider_id);

  const fetchSessions = async () => {
    try {
      const sess = await api.listSessions();
      setSessions(sess);
      if (!activeSession) {
        const first = sess.find(s => matchesTab(s, chatTab));
        if (first) selectSession(first);
      }
    } catch (err) {
      console.error('Failed to load sessions', err);
    }
  };

  const createSession = async () => {
    try {
      const sess = await api.createSession('');
      setSessions([sess, ...sessions]);
      selectSession(sess);
    } catch (err) {
      console.error('Failed to create session', err);
    }
  };

  const deleteSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await api.deleteSession(id);
      const remaining = sessions.filter(s => s.id !== id);
      setSessions(remaining);
      if (activeSession?.id === id) {
        setActiveSession(null);
        setMessages([]);
        if (remaining.length > 0) selectSession(remaining[0]);
      }
    } catch (err) {
      console.error('Failed to delete session', err);
    }
  };

  const selectSession = async (sess: Session) => {
    setActiveSession(sess);
    setJitItems([]);
    setActivities([]);  // restore effect repopulates if this session has a running turn
    try {
      const msgs = await api.listMessages(sess.id);
      setMessages(msgs);
      autoScrollRef.current = true;
      scrollToBottom('auto', true);
    } catch (err) {
      console.error('Failed to load messages', err);
    }
  };

  // Switch between the "App", "Channels", and "Tasks" chat groups. If the
  // currently active session isn't part of the target group, jump to the first
  // one that is (or clear the view when the group is empty).
  const switchTab = (tab: ChatTab) => {
    setChatTab(tab);
    const inTab = sessions.filter(s => matchesTab(s, tab));
    if (!activeSession || !inTab.some(s => s.id === activeSession.id)) {
      if (inTab.length > 0) {
        selectSession(inTab[0]);
      } else {
        setActiveSession(null);
        setMessages([]);
        setActivities([]);
        setJitItems([]);
      }
    }
  };

  const startRename = (sess: Session, e: React.MouseEvent) => {
    e.stopPropagation();
    setRenamingId(sess.id);
    setRenameDraft(sess.title || '');
  };

  const commitRename = async () => {
    if (!renamingId) return;
    const title = renameDraft.trim();
    setRenamingId(null);
    if (!title) return;
    try {
      const updated = await api.updateSession(renamingId, { title });
      setSessions(prev => prev.map(s => (s.id === updated.id ? updated : s)));
      if (activeSession?.id === updated.id) setActiveSession(updated);
    } catch (err) {
      console.error('Failed to rename session', err);
    }
  };

  const resolveJit = async (item: JitChatItem, action: 'approve' | 'deny', opts?: JitGrantOpts) => {
    setJitItems(prev => prev.map(j => (j.id === item.id ? { ...j, resolving: true } : j)));
    try {
      if (action === 'approve') {
        await api.approveJitRequest(item.id, opts);
      } else {
        await api.denyJitRequest(item.id);
      }
      setJitItems(prev => prev.map(j =>
        j.id === item.id
          ? { ...j, resolving: false, resolution: action === 'approve' ? 'approved' : 'denied' }
          : j,
      ));
    } catch (err) {
      console.error('Failed to resolve JIT request', err);
      setJitItems(prev => prev.map(j => (j.id === item.id ? { ...j, resolving: false } : j)));
    }
  };

  // Jump to the newest content. `force` ignores the auto-scroll lock — used
  // when the user sends a message or switches sessions, where pinning to the
  // bottom is always the right behavior.
  const scrollToBottom = (behavior: ScrollBehavior = 'smooth', force = false) => {
    if (!force && !autoScrollRef.current) return;
    const el = messagesContainerRef.current;
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior });
    });
  };

  // Track whether the user is parked at the bottom. Scrolling up cancels
  // auto-scroll; returning to within a small threshold of the bottom re-arms it.
  const handleMessagesScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    autoScrollRef.current = distanceFromBottom <= 80;
  };

  // Keep pinned to the bottom as messages and live activity stream in,
  // unless the user has scrolled up.
  useEffect(() => {
    scrollToBottom('auto');
  }, [messages, activities, jitItems, loading]);

  // Add an activity event to the live feed
  const addActivity = useCallback((type: SSEEventType, data: SSEEventData) => {
    setActivities(prev => [...prev, {
      id: nextActivityId(),
      type,
      data,
      timestamp: Date.now(),
    }]);
  }, []);

  const sendMessage = async () => {
    if (!input.trim() || isBusy) return;

    const trimmed = input.trim();

    // Slash command dispatch — available even without a session or provider
    if (trimmed.startsWith('/')) {
      const parts = trimmed.slice(1).trim().split(/\s+/);
      const cmdName = parts[0].toLowerCase();
      const cmdArgs = parts.slice(1).join(' ');
      setInput('');
      setCmdPopup(null);
      await dispatchCommand(cmdName, cmdArgs);
      return;
    }

    if (!activeSession || loading) return;

    if (!orchConfig?.provider_id) {
      setShowConfig(true);
      return;
    }

    const userMsg = trimmed;
    setInput('');
    setMention(null);
    setSysNotices([]);
    setActivities([]); // Clear previous activities
    setJitItems(prev => prev.filter(j => !j.resolution)); // Keep only unresolved JIT cards

    const tempUserMsg: Msg = {
      id: 'temp-' + Date.now(),
      session_id: activeSession.id,
      role: 'user',
      content: userMsg,
      operator_id: null,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, tempUserMsg]);
    setLoading(true);
    // Sending a message always re-pins to the bottom.
    autoScrollRef.current = true;
    scrollToBottom('smooth', true);

    // Start SSE stream to capture live events
    const stream = new ChatStream(activeSession.id);
    streamRef.current = stream;

    stream.on('thinking', (data) => addActivity('thinking', data));
    stream.on('delegation_start', (data) => addActivity('delegation_start', data));
    stream.on('tool_call', (data) => addActivity('tool_call', data));
    stream.on('tool_result', (data) => addActivity('tool_result', data));
    stream.on('delegation_result', (data) => addActivity('delegation_result', data));
    stream.on('text_delta', (data) => addActivity('text_delta', data));
    stream.on('jit_request', (data) => {
      const jitId = data.id;
      if (!jitId) return;
      setJitItems(prev => prev.some(j => j.id === jitId) ? prev : [...prev, {
        id: jitId,
        operator_name: data.operator_name,
        resource: data.resource,
        permission: data.permission,
        task_description: data.task_description,
      }]);
    });
    stream.on('error', (data) => addActivity('error', data));

    stream.on('done', () => {
      stream.close();
      streamRef.current = null;
    });

    // Small delay to ensure SSE endpoint is ready before the POST completes
    setTimeout(() => stream.start(), 100);

    try {
      await api.sendMessage(activeSession.id, { content: userMsg });
      const msgs = await api.listMessages(activeSession.id);
      setMessages(msgs);
      // Refresh the sidebar — the first message auto-titles the chat
      api.listSessions().then(setSessions).catch(() => {});
      scrollToBottom();
    } catch (err: any) {
      const errMsg: Msg = {
        id: 'error-' + Date.now(),
        session_id: activeSession.id,
        role: 'assistant',
        content: `Error: ${err.message || 'Failed to get response. Configure the orchestrator provider first.'}`,
        operator_id: null,
        created_at: new Date().toISOString(),
      };
      setMessages(prev => [...prev, errMsg]);
    } finally {
      setLoading(false);
      if (streamRef.current) {
        streamRef.current.close();
        streamRef.current = null;
      }
    }
  };

  // ── @operator mention autocomplete ──────────────────────────────
  // Find an active "@…" token ending at the caret: the last '@' that sits at
  // the start of the line or after whitespace. Operator names contain spaces,
  // so the query runs to the caret and is matched against full names.
  const detectMention = (value: string, caret: number): { start: number; query: string } | null => {
    const upto = value.slice(0, caret);
    let at = -1;
    for (let i = upto.length - 1; i >= 0; i--) {
      const ch = upto[i];
      if (ch === '\n') break;
      if (ch === '@') {
        if (i === 0 || /\s/.test(upto[i - 1])) at = i;
        break;
      }
    }
    if (at === -1) return null;
    return { start: at, query: upto.slice(at + 1) };
  };

  const mentionMatches = mention
    ? operators
        .filter(o => o.enabled && o.name.toLowerCase().includes(mention.query.toLowerCase()))
        .slice(0, 6)
    : [];
  const showMention = !!mention && mentionMatches.length > 0;

  // ── /command autocomplete ────────────────────────────────────────
  const detectCommand = (value: string, caret: number): { start: number; query: string } | null => {
    const upto = value.slice(0, caret);
    let slashPos = -1;
    for (let i = upto.length - 1; i >= 0; i--) {
      const ch = upto[i];
      if (/\s/.test(ch)) break;
      if (ch === '/') {
        if (i === 0 || /\s/.test(upto[i - 1])) slashPos = i;
        break;
      }
    }
    if (slashPos === -1) return null;
    return { start: slashPos, query: upto.slice(slashPos + 1) };
  };

  const cmdMatches = cmdPopup
    ? commands
        .filter(c =>
          c.name.startsWith(cmdPopup.query.toLowerCase()) ||
          c.summary.toLowerCase().includes(cmdPopup.query.toLowerCase()),
        )
        .slice(0, 8)
    : [];
  const showCmdPopup = !!cmdPopup && cmdMatches.length > 0 && !showMention;

  const applyCommand = (cmd: CommandSpec) => {
    if (!cmdPopup) return;
    const before = input.slice(0, cmdPopup.start);
    const after = input.slice(cmdPopup.start + 1 + cmdPopup.query.length);
    const insert = `/${cmd.name} `;
    setInput(before + insert + after);
    setCmdPopup(null);
    const caret = (before + insert).length;
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (ta) { ta.focus(); ta.setSelectionRange(caret, caret); }
    });
  };

  // ── system notice helpers ────────────────────────────────────────
  const addSysNotice = (kind: 'markdown' | 'error', text: string) => {
    setSysNotices(prev => [...prev, { id: 'sys-' + Date.now() + '-' + Math.random(), kind, text }]);
  };

  const handleCommandResult = useCallback((result: CommandResult) => {
    switch (result.kind) {
      case 'markdown':
      case 'error':
        addSysNotice(result.kind, result.text);
        break;
      case 'session_created': {
        const sess = result.data?.session as Session | undefined;
        if (sess) {
          setSessions(prev => [sess, ...prev.filter(s => s.id !== sess.id)]);
          selectSession(sess);
        }
        if (result.text) addSysNotice('markdown', result.text);
        break;
      }
      case 'session_switch': {
        const sess = result.data?.session as Session | undefined;
        if (sess) {
          setSessions(prev => {
            if (prev.some(s => s.id === sess.id)) return prev;
            return [sess, ...prev];
          });
          selectSession(sess);
        }
        break;
      }
      case 'session_deleted': {
        const sid = result.data?.session_id as string | undefined;
        if (sid) {
          setSessions(prev => prev.filter(s => s.id !== sid));
          if (activeSession?.id === sid) {
            setActiveSession(null);
            setMessages([]);
            setSysNotices([]);
          }
        }
        if (result.text) addSysNotice('markdown', result.text);
        break;
      }
      case 'config_changed':
        loadOrchestratorConfig();
        api.listProviders().then(setProviders).catch(() => {});
        if (result.text) addSysNotice('markdown', result.text);
        break;
      case 'stopped':
        setLoading(false);
        if (result.text) addSysNotice('markdown', result.text);
        break;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSession?.id]);

  const dispatchCommand = async (name: string, args: string) => {
    if (name === 'clear') {
      setSysNotices([]);
      setMessages([]);
      return;
    }
    if (name === 'logout') {
      await api.logout().catch(() => {});
      window.location.href = '/login';
      return;
    }
    if (name === 'login') {
      setShowWizard(true);
      return;
    }
    if (name === 'quit') {
      addSysNotice('markdown', '`/quit` is not available in the web app.');
      return;
    }
    try {
      const result = await api.runCommand({
        command: name,
        args,
        session_id: activeSession?.id ?? null,
      });
      handleCommandResult(result);
    } catch (err: any) {
      addSysNotice('error', err.message || 'Command failed.');
    }
  };

  const onInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setInput(value);
    const caret = e.target.selectionStart ?? value.length;
    setMention(detectMention(value, caret));
    setCmdPopup(detectCommand(value, caret));
    setMentionIndex(0);
    setCmdIndex(0);
  };

  const applyMention = (op: Operator) => {
    if (!mention) return;
    const before = input.slice(0, mention.start);
    const after = input.slice(mention.start + 1 + mention.query.length);
    const insert = `@${op.name} `;
    setInput(before + insert + after);
    setMention(null);
    const caret = (before + insert).length;
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (ta) { ta.focus(); ta.setSelectionRange(caret, caret); }
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (showMention) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setMentionIndex(i => (i + 1) % mentionMatches.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setMentionIndex(i => (i - 1 + mentionMatches.length) % mentionMatches.length);
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        applyMention(mentionMatches[mentionIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setMention(null);
        return;
      }
    }
    if (showCmdPopup) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCmdIndex(i => (i + 1) % cmdMatches.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCmdIndex(i => (i - 1 + cmdMatches.length) % cmdMatches.length);
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        applyCommand(cmdMatches[cmdIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setCmdPopup(null);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const renderMessageContent = (content: unknown): string => {
    if (typeof content === 'string') {
      try {
        const parsed = JSON.parse(content);
        if (parsed.delegation) {
          return parsed.text || content;
        }
        if (parsed.operator && parsed.result) {
          return `[${parsed.operator}] ${parsed.result}`;
        }
      } catch {}
      return content;
    }
    if (content && typeof content === 'object') {
      const obj = content as Record<string, unknown>;
      if ('delegation' in obj && 'text' in obj) {
        return String(obj.text) || '';
      }
      if ('operator' in obj) {
        const operator = obj.operator as string;
        const result = obj.result as string;
        const status = obj.status as string;
        return `[${operator}] ${(status === 'error' ? '❌ ' : '')}${result || 'No result'}`;
      }
      if ('content' in obj) return String(obj.content);
      return JSON.stringify(obj, null, 2);
    }
    return String(content);
  };

  const isDelegationResult = (msg: Msg): boolean => {
    if (typeof msg.content === 'string') {
      try {
        const parsed = JSON.parse(msg.content);
        return !!(parsed.operator && parsed.result);
      } catch { return false; }
    }
    return msg.role === 'tool' ||
      (typeof msg.content === 'object' && msg.content !== null && 'operator' in msg.content);
  };

  // ── Activity Feed Rendering ──────────────────────────────────────

  const renderActivityIcon = (type: SSEEventType, data?: any) => {
    if (data?.tool === 'web_search') return <Search className="w-3.5 h-3.5 text-sky-400" />;
    if (data?.tool === 'web_fetch') return <Globe className="w-3.5 h-3.5 text-sky-400" />;
    switch (type) {
      case 'thinking': return <Cpu className="w-3.5 h-3.5 text-blue-400 animate-pulse" />;
      case 'delegation_start': return <Zap className="w-3.5 h-3.5 text-amber-400" />;
      case 'tool_call': return <Wrench className="w-3.5 h-3.5 text-purple-400" />;
      case 'tool_result': return <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />;
      case 'delegation_result': return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />;
      case 'error': return <AlertCircle className="w-3.5 h-3.5 text-red-400" />;
      default: return <Activity className="w-3.5 h-3.5 text-text-secondary" />;
    }
  };

  const renderActivityText = (act: ActivityEvent): string => {
    const d = act.data;
    switch (act.type) {
      case 'thinking':
        return `Thinking... (iteration ${d.iteration || '?'})`;
      case 'delegation_start':
        return `Delegating to ${d.operator || 'operator'}: ${(d.task || '').slice(0, 80)}`;
      case 'tool_call':
        if (d.tool === 'web_search') return `Searching: ${(d.query || '').slice(0, 80)}`;
        if (d.tool === 'web_fetch') return `Reading: ${(d.url || '').slice(0, 80)}`;
        return `Running ${d.tool || 'tool'} via ${d.operator || 'operator'}`;
      case 'tool_result':
        return `${d.tool || 'Tool'}: ${(d.preview || 'completed').slice(0, 100)}`;
      case 'delegation_result':
        return `${d.operator || 'Operator'} finished (${d.status || 'done'})`;
      case 'text_delta':
        return d.text ? (d.text.length > 100 ? d.text.slice(0, 100) + '...' : d.text) : '';
      case 'error':
        return `Error: ${(d.error || '').slice(0, 100)}`;
      default:
        return JSON.stringify(d);
    }
  };

  const appCount = sessions.filter(s => matchesTab(s, 'app')).length;
  const channelCount = sessions.filter(s => isExternalSession(s)).length;
  const taskCount = sessions.filter(s => isScheduledSession(s)).length;
  const visibleSessions = sessions.filter(s => matchesTab(s, chatTab));

  return (
    <div className="flex h-full overflow-hidden bg-white dark:bg-surface">
      {/* Sidebar */}
      <div className="w-[260px] shrink-0 border-r border-border dark:border-border bg-surface/30 dark:bg-surface/30 flex flex-col">
        {/* App / Channels tab switcher */}
        <div className="p-2 border-b border-border dark:border-border">
          <div className="flex items-center gap-1 p-0.5 rounded-md bg-surface dark:bg-surface border border-border dark:border-border">
            <button
              onClick={() => switchTab('app')}
              className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 text-[12px] rounded transition-colors ${
                chatTab === 'app'
                  ? 'bg-white dark:bg-bg text-text-primary dark:text-text-primary shadow-sm font-medium'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              <MessageSquare className="w-3.5 h-3.5" />
              App{appCount > 0 ? ` (${appCount})` : ''}
            </button>
            <button
              onClick={() => switchTab('channels')}
              className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 text-[12px] rounded transition-colors ${
                chatTab === 'channels'
                  ? 'bg-white dark:bg-bg text-text-primary dark:text-text-primary shadow-sm font-medium'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              <Share2 className="w-3.5 h-3.5" />
              Channels{channelCount > 0 ? ` (${channelCount})` : ''}
            </button>
            <button
              onClick={() => switchTab('tasks')}
              className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 text-[12px] rounded transition-colors ${
                chatTab === 'tasks'
                  ? 'bg-white dark:bg-bg text-text-primary dark:text-text-primary shadow-sm font-medium'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              <CalendarClock className="w-3.5 h-3.5" />
              Tasks{taskCount > 0 ? ` (${taskCount})` : ''}
            </button>
          </div>
        </div>

        {chatTab === 'app' ? (
          <div className="p-2 pb-0">
            <button
              onClick={createSession}
              className="w-full flex items-center justify-center py-2 px-4 rounded text-[13px] font-medium bg-accent text-white hover:bg-accent-hover transition-colors"
            >
              <MessageSquare className="w-4 h-4 mr-2" />
              New Chat
            </button>
          </div>
        ) : chatTab === 'channels' ? (
          <div className="px-3 pt-3 pb-1 text-[11px] text-text-secondary/70 leading-relaxed">
            Chats from Telegram &amp; Discord appear here. Start one by messaging
            the bot from that platform.
          </div>
        ) : (
          <div className="px-3 pt-3 pb-1 text-[11px] text-text-secondary/70 leading-relaxed">
            Each scheduled task run creates a session here. Manage schedules on the{' '}
            <a href="/tasks" className="text-accent hover:underline">Tasks</a> page.
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {visibleSessions.length === 0 && (
            <div className="px-3 py-6 text-center text-[12px] text-text-secondary/50">
              {chatTab === 'channels'
                ? 'No channel chats yet.'
                : chatTab === 'tasks'
                  ? 'No task runs yet.'
                  : 'No chats yet.'}
            </div>
          )}
          {visibleSessions.map(sess => (
            <div
              key={sess.id}
              onClick={() => selectSession(sess)}
              className={`group flex items-center rounded-md text-[13px] transition-colors cursor-pointer ${
                activeSession?.id === sess.id
                  ? 'bg-accent/10 dark:bg-accent/20 text-accent font-medium'
                  : 'text-text-secondary hover:text-text-primary hover:bg-surface dark:hover:bg-border'
              }`}
            >
              <div className="flex-1 min-w-0 px-3 py-2.5">
                {renamingId === sess.id ? (
                  <input
                    autoFocus
                    value={renameDraft}
                    onChange={e => setRenameDraft(e.target.value)}
                    onClick={e => e.stopPropagation()}
                    onKeyDown={e => {
                      if (e.key === 'Enter') commitRename();
                      if (e.key === 'Escape') setRenamingId(null);
                    }}
                    onBlur={commitRename}
                    className="w-full px-1.5 py-0.5 text-[13px] rounded border border-accent bg-white dark:bg-surface text-text-primary dark:text-text-primary outline-none"
                  />
                ) : (
                  <div className="truncate">{sess.title || 'Chat Session'}</div>
                )}
                <div className="flex items-center gap-1.5 text-[11px] text-text-secondary/60 mt-0.5">
                  {isExternalSession(sess) && (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-accent/10 text-accent">
                      {EXTERNAL_LABELS[sess.origin!] ?? sess.origin}
                    </span>
                  )}
                  <span>{new Date(sess.last_active_at).toLocaleDateString()}</span>
                </div>
              </div>
              <button
                onClick={(e) => startRename(sess, e)}
                className="shrink-0 p-1.5 rounded text-text-secondary/40 hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-all opacity-0 group-hover:opacity-100"
                title="Rename chat"
              >
                <Pencil className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={(e) => deleteSession(sess.id, e)}
                className="shrink-0 p-1.5 mr-2 rounded text-text-secondary/40 hover:text-danger hover:bg-danger/5 transition-all"
                title="Delete chat"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 min-w-0 flex flex-col">
        {activeSession ? (
          <>
            {/* Header */}
            <div className="px-6 py-3 border-b border-border dark:border-border flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  {currentProvider ? (
                    <span className="text-[13px]">
                      <span className="text-text-secondary">Model:</span>{' '}
                      <span className="font-medium text-text-primary dark:text-text-primary">
                        {orchConfig?.model || currentProvider.default_model || 'Not set'}
                      </span>
                    </span>
                  ) : (
                    <span className="text-[13px] italic text-warning">
                      No provider configured
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {/* Live tasks indicator + dropdown */}
                <div className="relative">
                  <button
                    onClick={() => setShowTasks(v => !v)}
                    className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] rounded transition-colors ${
                      runningTasks.length > 0
                        ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10'
                        : 'text-text-secondary hover:text-text-primary hover:bg-surface'
                    }`}
                    title="Running tasks"
                  >
                    {runningTasks.length > 0
                      ? <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                      : <ListChecks className="w-3.5 h-3.5" />}
                    Tasks{runningTasks.length > 0 ? ` (${runningTasks.length})` : ''}
                  </button>
                  {showTasks && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setShowTasks(false)} />
                      <div className="absolute right-0 top-full mt-1.5 w-[320px] z-20 rounded-lg border border-border dark:border-border bg-white dark:bg-surface shadow-xl overflow-hidden">
                        <div className="px-3 py-2 text-[11px] font-medium text-text-secondary uppercase tracking-wider border-b border-border dark:border-border">
                          Running tasks
                        </div>
                        {runningTasks.length === 0 ? (
                          <div className="px-3 py-6 text-center text-[12px] text-text-secondary/60">
                            Nothing running right now.
                          </div>
                        ) : (
                          <div className="max-h-[320px] overflow-y-auto divide-y divide-border dark:divide-border">
                            {runningTasks.map(t => (
                              <div key={t.id} className="px-3 py-2.5">
                                <div className="flex items-start justify-between gap-2">
                                  <div className="min-w-0">
                                    <div className="text-[13px] font-medium text-text-primary dark:text-text-primary truncate">{t.title}</div>
                                    <div className="text-[11px] text-text-secondary truncate mt-0.5">
                                      {t.cancelling ? 'Stopping…' : t.current_step}
                                    </div>
                                    <div className="text-[10px] text-text-secondary/50 mt-0.5">
                                      {Math.round(t.elapsed_seconds)}s elapsed
                                      {t.operator ? ` · ${t.operator}` : ''}
                                    </div>
                                  </div>
                                  <button
                                    onClick={() => cancelTask(t.session_id)}
                                    disabled={cancelling[t.session_id] || t.cancelling}
                                    className="shrink-0 flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded bg-danger/10 text-danger hover:bg-danger/20 disabled:opacity-50 transition-colors"
                                  >
                                    {cancelling[t.session_id] || t.cancelling
                                      ? <Loader2 className="w-3 h-3 animate-spin" />
                                      : <Square className="w-3 h-3" />}
                                    Stop
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
                <button
                  onClick={() => setShowActivity(v => !v)}
                  className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] rounded transition-colors ${
                    showActivity
                      ? 'text-accent bg-accent/[0.07] dark:bg-accent/20'
                      : 'text-text-secondary hover:text-text-primary hover:bg-surface'
                  }`}
                  title={showActivity ? 'Hide actions panel' : 'Show actions panel'}
                >
                  <Activity className="w-3.5 h-3.5" />
                  Actions
                </button>
                <button
                  onClick={() => setShowConfig(!showConfig)}
                  className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] rounded transition-colors ${
                    showConfig
                      ? 'text-accent bg-accent/[0.07] dark:bg-accent/20'
                      : 'text-text-secondary hover:text-text-primary hover:bg-surface'
                  }`}
                >
                  <Settings className="w-3.5 h-3.5" />
                  Orchestrator
                </button>
                <button
                  onClick={(e) => deleteSession(activeSession.id, e)}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-text-secondary hover:text-danger hover:bg-danger/5 rounded transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Delete
                </button>
              </div>
            </div>

            {/* Orchestrator Config Drawer */}
            {showConfig && (
              <div className="px-6 py-4 border-b border-border dark:border-border bg-surface/20 dark:bg-bg/50">
                <div className="flex items-center justify-between mb-3">
                  <div className="text-[12px] font-medium text-text-secondary uppercase tracking-wider">
                    Vigilus Orchestrator Config
                  </div>
                  <div className="flex items-center gap-1 p-0.5 rounded-md bg-surface dark:bg-surface border border-border dark:border-border">
                    <button
                      onClick={() => setConfigTab('provider')}
                      className={`flex items-center gap-1.5 px-2.5 py-1 text-[12px] rounded transition-colors ${
                        configTab === 'provider'
                          ? 'bg-white dark:bg-surface text-text-primary dark:text-text-primary shadow-sm font-medium'
                          : 'text-text-secondary hover:text-text-primary'
                      }`}
                    >
                      <Settings className="w-3.5 h-3.5" /> Provider
                    </button>
                    <button
                      onClick={() => setConfigTab('soul')}
                      className={`flex items-center gap-1.5 px-2.5 py-1 text-[12px] rounded transition-colors ${
                        configTab === 'soul'
                          ? 'bg-white dark:bg-surface text-text-primary dark:text-text-primary shadow-sm font-medium'
                          : 'text-text-secondary hover:text-text-primary'
                      }`}
                    >
                      <Brain className="w-3.5 h-3.5" /> Soul & Memory
                    </button>
                  </div>
                </div>
                {configTab === 'soul' && (
                  <div className="space-y-4 mb-3">
                    <div>
                      <label className="block text-[11px] text-text-secondary mb-1">
                        Soul — Vigilus's persona, carried into every conversation
                      </label>
                      <textarea
                        value={soulDraft}
                        onChange={e => setSoulDraft(e.target.value)}
                        rows={3}
                        placeholder="e.g. You are calm and methodical. You know this is a small homelab — prefer simple fixes over enterprise ceremony, and explain what you did in plain language."
                        className="w-full px-3 py-2 text-[13px] rounded border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary placeholder:text-text-secondary/40 resize-y"
                      />
                      <div className="flex items-center gap-2 mt-2">
                        <button
                          onClick={saveOrchConfig}
                          className="px-3 py-1.5 text-[12px] rounded bg-accent text-white hover:bg-accent-hover transition-colors"
                        >
                          Save Soul
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[11px] text-text-secondary mb-1">
                        Memory — facts Vigilus and its operators have learned about your environment
                      </label>
                      <MemoryPanel scopes={['global', 'orchestrator']} privateScopeLabel="Vigilus" />
                    </div>
                  </div>
                )}
                {configTab === 'provider' && (<>
                <div className="grid grid-cols-2 gap-3 mb-3">
                  <div>
                    <label className="block text-[11px] text-text-secondary mb-1">LLM Provider</label>
                    <select
                      value={selectedProviderId}
                      onChange={e => {
                        setSelectedProviderId(e.target.value);
                        setSelectedModel('');
                      }}
                      className="w-full px-2.5 py-1.5 text-[13px] rounded border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary"
                    >
                      <option value="">Select provider…</option>
                      {providers.filter(p => p.enabled).map(p => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-[11px] text-text-secondary mb-1">
                      Model {loadingModels && <span className="opacity-60">(loading…)</span>}
                    </label>
                    {models.length > 0 ? (
                      <select
                        value={selectedModel}
                        onChange={e => setSelectedModel(e.target.value)}
                        className="w-full px-2.5 py-1.5 text-[13px] rounded border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary"
                      >
                        <option value="">Use provider default</option>
                        {models.map(m => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={selectedModel}
                        onChange={e => setSelectedModel(e.target.value)}
                        placeholder="Model name (or leave empty)"
                        className="w-full px-2.5 py-1.5 text-[13px] rounded border border-border dark:border-border bg-white dark:bg-surface text-text-primary dark:text-text-primary placeholder:text-text-secondary/40"
                      />
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={saveOrchConfig}
                    disabled={!selectedProviderId}
                    className="px-3 py-1.5 text-[12px] rounded bg-accent text-white hover:bg-accent-hover disabled:opacity-50 disabled:hover:bg-accent transition-colors"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setShowConfig(false)}
                    className="px-3 py-1.5 text-[12px] rounded text-text-secondary hover:text-text-primary hover:bg-surface transition-colors"
                  >
                    Cancel
                  </button>
                  {!orchConfig?.provider_id && (
                    <span className="text-[11px] text-warning">
                      ⚠ A provider is required for Vigilus to work.
                    </span>
                  )}
                </div>
                </>)}
              </div>
            )}

            {/* Messages + Activity Feed */}
            <div className="flex-1 min-w-0 flex overflow-hidden">
              {/* Messages Column */}
              <div
                ref={messagesContainerRef}
                onScroll={handleMessagesScroll}
                className="flex-1 min-w-0 overflow-y-auto p-6 space-y-4"
              >
                {messages.length === 0 && !isBusy ? (
                  <div className="h-full flex flex-col items-center justify-center text-text-secondary dark:text-text-secondary">
                    <Bot className="w-12 h-12 mb-4 opacity-50" />
                    <p className="text-[14px]">This is the beginning of your conversation with Vigilus.</p>
                    {!orchConfig?.provider_id && (
                      <button
                        onClick={() => setShowConfig(true)}
                        className="mt-4 text-[13px] text-accent hover:text-accent/80"
                      >
                        Configure the orchestrator to get started →
                      </button>
                    )}
                  </div>
                ) : (
                  messages
                    // Skip bubbles with nothing to show — e.g. an assistant
                    // message whose only content was the (stripped) delegation
                    // JSON renders as an empty box otherwise.
                    .filter(msg => renderMessageContent(msg.content).trim().length > 0)
                    .map((msg, i) => (
                    <div key={msg.id || i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div className={`flex max-w-[85%] gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
                          msg.role === 'user'
                            ? 'bg-surface border border-border text-text-secondary'
                            : isDelegationResult(msg)
                            ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400'
                            : 'bg-accent/10 text-accent'
                        }`}>
                          {msg.role === 'user' ? <User className="w-4 h-4" /> :
                           isDelegationResult(msg) ? <Wrench className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
                        </div>

                        <div className={`flex flex-col min-w-0 max-w-full ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                          <span className="text-[11px] text-text-secondary mb-1 tracking-wide font-medium">
                            {msg.role === 'user' ? 'You' :
                             isDelegationResult(msg)
                               ? `Operator: ${(msg.content as any)?.operator || 'result'}`
                               : 'Vigilus'}
                          </span>
                          {isDelegationResult(msg) ? (
                            <div className="max-w-full px-4 py-3 rounded-lg text-[13px] bg-surface dark:bg-surface border border-border dark:border-border font-mono whitespace-pre-wrap break-words overflow-x-auto text-text-secondary">
                              {renderMessageContent(msg.content)}
                            </div>
                          ) : msg.role === 'user' ? (
                            <div className="max-w-full px-4 py-3 rounded-2xl text-[14px] leading-relaxed break-words whitespace-pre-wrap bg-accent text-white rounded-tr-sm">
                              {renderMessageContent(msg.content)}
                            </div>
                          ) : (
                            <div className="max-w-full px-4 py-3 rounded-2xl bg-surface dark:bg-surface text-text-primary dark:text-text-primary border border-border dark:border-border rounded-tl-sm">
                              <Markdown>{renderMessageContent(msg.content)}</Markdown>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))
                )}

                {/* System notices from slash command results */}
                {sysNotices.map(notice => (
                  <div key={notice.id} className="flex justify-center">
                    <div className={`flex items-start gap-2 px-4 py-2.5 rounded-lg text-[12px] max-w-[85%] ${
                      notice.kind === 'error'
                        ? 'bg-danger/5 border border-danger/20 text-danger'
                        : 'bg-surface dark:bg-surface border border-border dark:border-border text-text-secondary'
                    }`}>
                      {notice.kind === 'error'
                        ? <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                        : <Terminal className="w-3.5 h-3.5 shrink-0 mt-0.5" />}
                      <Markdown>{notice.text}</Markdown>
                    </div>
                  </div>
                ))}

                {/* Inline JIT approval cards */}
                {jitItems.map(item => (
                  <div key={item.id} className="flex justify-start">
                    <div className="flex max-w-[85%] gap-3">
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
                        item.resolution === 'approved'
                          ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                          : item.resolution === 'denied'
                          ? 'bg-red-500/10 text-red-600 dark:text-red-400'
                          : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                      }`}>
                        <KeyRound className="w-4 h-4" />
                      </div>
                      <div className="flex flex-col items-start">
                        <span className="text-[11px] text-text-secondary mb-1 tracking-wide font-medium">
                          JIT Approval Request
                        </span>
                        <div className={`px-4 py-3 rounded-lg text-[13px] border ${
                          item.resolution
                            ? 'bg-surface/50 dark:bg-surface/50 border-border dark:border-border'
                            : 'bg-amber-500/5 border-amber-500/30'
                        }`}>
                          <div className="text-text-primary dark:text-text-primary mb-1">
                            <span className="font-medium">{item.operator_name || 'An operator'}</span>
                            {' '}needs <span className="font-mono font-medium">{item.permission || 'elevated'}</span> access
                            {item.resource && item.resource !== '*' && (
                              <> to <span className="font-mono">{item.resource}</span></>
                            )}
                          </div>
                          {item.task_description && (
                            <div className="text-[12px] text-text-secondary mb-2 max-w-[420px]">
                              {item.task_description}
                            </div>
                          )}
                          {item.resolution === 'approved' ? (
                            <div className="flex items-center gap-1.5 text-[12px] text-green-600 dark:text-green-400 font-medium">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              Approved — the operator is continuing automatically.
                            </div>
                          ) : item.resolution === 'denied' ? (
                            <div className="flex items-center gap-1.5 text-[12px] text-red-600 dark:text-red-400 font-medium">
                              <AlertCircle className="w-3.5 h-3.5" />
                              Denied — the operator will not perform this action.
                            </div>
                          ) : (
                            <>
                              <div className="text-[11px] text-amber-600 dark:text-amber-400 mb-2">
                                The operator is paused, waiting for your decision.
                              </div>
                              <JitGrantControls
                                resource={item.resource}
                                busy={item.resolving}
                                onApprove={opts => resolveJit(item, 'approve', opts)}
                                onDeny={() => resolveJit(item, 'deny')}
                              />
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}

                {/* Loading indicator — shows activity feed during processing */}
                {isBusy && (
                  <div className="flex justify-start">
                    <div className="flex max-w-[80%] gap-3 flex-row">
                      <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-accent/10 text-accent">
                        <Bot className="w-4 h-4 animate-pulse" />
                      </div>
                      <div className="flex items-start px-4 py-3 rounded-2xl bg-surface dark:bg-surface border border-border dark:border-border rounded-tl-sm">
                        {activities.length > 0 ? (
                          <div className="space-y-1.5 max-w-[400px]">
                            {activities.slice(-5).map(act => (
                              <div key={act.id} className="flex items-center gap-2 text-[12px]">
                                {renderActivityIcon(act.type, act.data)}
                                <span className="text-text-secondary truncate">
                                  {renderActivityText(act)}
                                </span>
                              </div>
                            ))}
                            {activities.length > 5 && (
                              <div className="text-[11px] text-text-secondary/60">
                                +{activities.length - 5} earlier events
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="flex gap-1.5">
                            <div className="w-1.5 h-1.5 rounded-full bg-accent/60 animate-bounce" />
                            <div className="w-1.5 h-1.5 rounded-full bg-accent/60 animate-bounce" style={{ animationDelay: '150ms' }} />
                            <div className="w-1.5 h-1.5 rounded-full bg-accent/60 animate-bounce" style={{ animationDelay: '300ms' }} />
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Actions panel — persistent, toggled from the header so it
                  never pops in and out mid-turn. */}
              {showActivity && (
                <div className="w-[280px] shrink-0 border-l border-border dark:border-border bg-surface/30 dark:bg-bg/50 overflow-y-auto flex flex-col">
                  <div className="px-3 py-2.5 border-b border-border dark:border-border sticky top-0 bg-surface/80 dark:bg-bg/80 backdrop-blur-sm flex items-center justify-between">
                    <div className="flex items-center gap-2 text-[12px] font-medium text-text-secondary uppercase tracking-wider">
                      <Activity className="w-3.5 h-3.5" />
                      Actions
                      {isBusy && (
                        <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" title="Running" />
                      )}
                    </div>
                    <button
                      onClick={() => setShowActivity(false)}
                      className="p-0.5 rounded text-text-secondary/50 hover:text-text-primary hover:bg-surface dark:hover:bg-border transition-colors"
                      title="Hide actions panel"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  {activities.length > 0 ? (
                    <div className="p-2 space-y-1">
                      {activities.map(act => (
                        <div
                          key={act.id}
                          className="flex items-start gap-2 px-2 py-1.5 rounded text-[11px] hover:bg-surface/50 dark:hover:bg-border/50"
                        >
                          <div className="mt-0.5 shrink-0">{renderActivityIcon(act.type, act.data)}</div>
                          <div className="min-w-0">
                            <div className="text-text-secondary leading-tight break-words">
                              {renderActivityText(act)}
                            </div>
                            <div className="text-text-secondary/40 mt-0.5">
                              {new Date(act.timestamp).toLocaleTimeString()}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="flex-1 flex items-center justify-center p-4 text-center text-[11px] text-text-secondary/50">
                      Actions taken by Vigilus and its operators will appear here as it works.
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Input */}
            <div className="p-4 border-t border-border dark:border-border bg-white dark:bg-surface">
              {!orchConfig?.provider_id && (
                <div className="mb-3 px-4 py-2 text-[12px] text-warning bg-warning/5 rounded-lg text-center">
                  No LLM provider configured.{' '}
                  <button onClick={() => setShowWizard(true)} className="font-medium hover:underline">Add one with /login →</button>
                  {' '}or{' '}
                  <button onClick={() => setShowConfig(true)} className="font-medium hover:underline">configure manually →</button>
                </div>
              )}
              <div className="relative">
                {/* /command autocomplete popup */}
                {showCmdPopup && (
                  <div className="absolute bottom-full left-0 mb-2 w-[380px] max-h-[280px] overflow-y-auto rounded-lg border border-border dark:border-border bg-white dark:bg-surface shadow-xl z-20">
                    <div className="px-3 py-1.5 text-[10px] font-medium text-text-secondary uppercase tracking-wider border-b border-border dark:border-border">
                      Commands
                    </div>
                    {cmdMatches.map((cmd, i) => (
                      <button
                        key={cmd.name}
                        type="button"
                        onMouseDown={e => { e.preventDefault(); applyCommand(cmd); }}
                        onMouseEnter={() => setCmdIndex(i)}
                        className={`w-full flex items-center gap-3 px-3 py-2 text-left transition-colors ${
                          i === cmdIndex
                            ? 'bg-accent/10 dark:bg-accent/20'
                            : 'hover:bg-surface dark:hover:bg-[#222]'
                        }`}
                      >
                        <span className="w-[100px] shrink-0 font-mono text-[12px] text-accent font-medium truncate">/{cmd.name}</span>
                        <div className="min-w-0 flex-1">
                          <div className="text-[12px] text-text-primary dark:text-text-primary truncate">{cmd.summary}</div>
                          {cmd.usage !== `/${cmd.name}` && (
                            <div className="text-[10px] text-text-secondary font-mono truncate">{cmd.usage}</div>
                          )}
                        </div>
                        <span className={`ml-auto text-[10px] shrink-0 px-1.5 py-0.5 rounded ${
                          cmd.execution === 'client'
                            ? 'bg-surface dark:bg-[#222] text-text-secondary'
                            : 'bg-accent/10 text-accent'
                        }`}>
                          {cmd.execution}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
                {/* @operator mention popout */}
                {showMention && (
                  <div className="absolute bottom-full left-0 mb-2 w-[280px] max-h-[240px] overflow-y-auto rounded-lg border border-border dark:border-border bg-white dark:bg-surface shadow-xl z-20">
                    <div className="px-3 py-1.5 text-[10px] font-medium text-text-secondary uppercase tracking-wider border-b border-border dark:border-border">
                      Delegate to operator
                    </div>
                    {mentionMatches.map((op, i) => (
                      <button
                        key={op.id}
                        type="button"
                        onMouseDown={e => { e.preventDefault(); applyMention(op); }}
                        onMouseEnter={() => setMentionIndex(i)}
                        className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${
                          i === mentionIndex
                            ? 'bg-accent/10 dark:bg-accent/20'
                            : 'hover:bg-surface dark:hover:bg-[#222]'
                        }`}
                      >
                        <div className="w-6 h-6 rounded-full bg-accent/10 text-accent flex items-center justify-center shrink-0">
                          <Bot className="w-3.5 h-3.5" />
                        </div>
                        <div className="min-w-0">
                          <div className="text-[13px] font-medium text-text-primary dark:text-text-primary truncate">{op.name}</div>
                          <div className="text-[11px] text-text-secondary truncate">{op.description}</div>
                        </div>
                        <span className="ml-auto text-[10px] text-text-secondary uppercase shrink-0">{op.permission_level}</span>
                      </button>
                    ))}
                  </div>
                )}
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={onInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder="Message Vigilus… (@ for operators, / for commands)"
                  className="w-full pl-4 pr-12 py-3 bg-surface/50 dark:bg-surface/50 border border-border dark:border-border rounded-xl text-[14px] text-text-primary focus:border-accent focus:ring-1 focus:ring-accent resize-none h-[52px] min-h-[52px] max-h-[150px] overflow-hidden leading-relaxed"
                  rows={1}
                  disabled={isBusy}
                />
                {isBusy ? (
                  <button
                    onClick={() => activeSession && cancelTask(activeSession.id)}
                    disabled={!activeSession || cancelling[activeSession?.id ?? '']}
                    title="Stop this task"
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg text-white bg-danger hover:bg-danger/90 disabled:opacity-60 transition-colors"
                  >
                    {cancelling[activeSession?.id ?? '']
                      ? <Loader2 className="w-4 h-4 animate-spin" />
                      : <Square className="w-4 h-4" />}
                  </button>
                ) : (
                  <button
                    onClick={sendMessage}
                    disabled={!input.trim()}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg text-white bg-accent hover:bg-accent-hover disabled:opacity-50 disabled:hover:bg-accent transition-colors"
                  >
                    <Send className="w-4 h-4 ml-0.5" />
                  </button>
                )}
              </div>
              <div className="flex items-center justify-center gap-1.5 text-[11px] text-text-secondary mt-2">
                <span>Enter to send · Shift+Enter for new line</span>
                <span className="text-text-secondary/40">·</span>
                <AtSign className="w-3 h-3" />
                <span>operators</span>
                <span className="text-text-secondary/40">·</span>
                <Terminal className="w-3 h-3" />
                <span>commands</span>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-text-secondary dark:text-text-secondary">
            <MessageSquare className="w-12 h-12 mb-4 opacity-50" />
            <p className="text-[14px]">Select a session or create a new chat to begin.</p>
          </div>
        )}
      </div>

      {/* Provider wizard — opened by /login or the "add provider" prompt */}
      {showWizard && (
        <ProviderWizard
          onClose={() => setShowWizard(false)}
          onComplete={provider => {
            setShowWizard(false);
            api.listProviders().then(setProviders).catch(() => {});
            loadOrchestratorConfig();
            addSysNotice('markdown', `**${provider.name}** added and ready.`);
          }}
        />
      )}
    </div>
  );
}
