import { useMemo, useCallback, useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import { graphlib, layout as dagreLayout } from '@dagrejs/dagre';
import '@xyflow/react/dist/style.css';
import {
  ShieldCheck,
  Radar,
  AlertTriangle,
  Wifi,
  Router,
  Globe,
  Network,
  RadioTower,
  Plus,
} from 'lucide-react';
import type { ScopeHostNode, ScopeSegment } from '@/types';
import { cn } from '@/lib/utils';
import { segmentColor, hexToRgba } from './colors';

// ── Layout constants ─────────────────────────────────────────
const COLS = 3;
const COL_W = 172; // host cell width (host box + gap)
const ROW_H = 100; // host cell height
const HEADER_H = 34; // segment header bar height
const PAD = 16; // group inner padding
const GATEWAY_W = 192;
const GATEWAY_H = 104;
const UNKNOWN_KEY = '__unknown__';

// ── Node data shapes ─────────────────────────────────────────
type HostNodeData = {
  node: ScopeHostNode;
  selected: boolean;
  onPromote: (ip: string) => void;
};

type GatewayNodeData = {
  node: ScopeHostNode;
  selected: boolean;
};

type SegmentNodeData = {
  cidr: string;
  label: string;
  color: string | null;
  width: number;
  height: number;
  unknown: boolean;
};

const GATEWAY_LAYOUT_KEY = '__gateway_layout__'; // internal dagre key only

// ── Gateway node (top-of-diagram, router icon, accent) ───────
function GatewayNode({ data }: NodeProps) {
  const { node, selected } = data as GatewayNodeData;
  return (
    <div
      className={cn(
        'relative px-4 py-2.5 rounded-lg border-2 bg-white dark:bg-surface min-w-[170px] shadow-md',
        selected ? 'ring-2 ring-accent' : 'border-accent',
      )}
    >
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <div className="flex items-center gap-1.5">
        <Router className="w-4 h-4 text-accent shrink-0" />
        <span className="text-[12px] font-semibold text-text-primary truncate max-w-[150px]">
          {node.role_label || node.label}
        </span>
      </div>
      {node.ip && (
        <div className="text-[10px] text-text-secondary font-mono mt-0.5">{node.ip}</div>
      )}
      {/* Badges — same shape as a host node. */}
      <div className="flex items-center gap-1 mt-1.5">
        {node.is_dns && (
          <span className="inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded bg-info/15 text-info">
            <Globe className="w-2.5 h-2.5" /> DNS
          </span>
        )}
        {node.finding_count > 0 && (
          <span
            className={cn(
              'inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded font-medium',
              node.finding_count > 2 ? 'bg-danger/15 text-danger' : 'bg-warning/15 text-warning',
            )}
          >
            <AlertTriangle className="w-2.5 h-2.5" /> {node.finding_count}
          </span>
        )}
        {node.open_port_count > 0 && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-surface text-text-secondary border border-border">
            {node.open_port_count} ports
          </span>
        )}
      </div>
    </div>
  );
}

// ── Segment group node (labeled, colored container) ─────────
function SegmentGroupNode({ data }: NodeProps) {
  const { label, color, width, height, unknown } = data as SegmentNodeData;
  const tint = unknown ? 'transparent' : hexToRgba(color || '#6b7280', 0.06);
  return (
    <div
      className="rounded-lg border-2"
      style={{
        width,
        height,
        borderColor: unknown ? 'rgb(var(--color-border))' : color || 'rgb(var(--color-border))',
        background: tint,
        borderStyle: unknown ? 'dashed' : 'solid',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div
        className="flex items-center h-[34px] px-3 rounded-t-md cursor-pointer select-none"
        style={{
          borderBottom: `1px solid ${unknown ? 'rgb(var(--color-border))' : color || 'rgb(var(--color-border))'}`,
          background: unknown ? 'transparent' : hexToRgba(color || '#6b7280', 0.12),
        }}
        title="Click to rename / recolor this segment"
      >
        <span className="text-[11px] font-semibold truncate" style={{ color: color || 'inherit' }}>
          {label}
        </span>
      </div>
    </div>
  );
}

// ── Host node (with role badges) ─────────────────────────────
function HostNode({ data }: NodeProps) {
  const { node, selected, onPromote } = data as HostNodeData;
  const managed = node.origins.includes('managed');
  const discovered = node.origins.includes('discovered');
  const monitored = node.monitored;

  return (
    <div
      className={cn(
        'relative px-3 py-2 rounded-lg border-2 bg-white dark:bg-surface min-w-[140px] transition-shadow',
        selected ? 'ring-2 ring-accent shadow-md' : 'shadow-sm',
        managed && discovered
          ? 'border-accent'
          : managed
            ? 'border-accent/70'
            : 'border-border border-dashed',
      )}
    >
      {!managed && node.ip && (
        <button
          className="nodrag nopan absolute -top-2 -right-2 w-5 h-5 rounded-full bg-accent text-white flex items-center justify-center shadow-sm hover:bg-accent-hover transition-colors"
          title="Add to Server Inventory"
          onClick={(e) => {
            e.stopPropagation();
            onPromote(node.ip!);
          }}
        >
          <Plus className="w-3 h-3" />
        </button>
      )}
      <div className="flex items-center gap-1.5">
        {managed ? (
          <ShieldCheck className="w-3.5 h-3.5 text-accent shrink-0" />
        ) : (
          <Radar className="w-3.5 h-3.5 text-text-secondary shrink-0" />
        )}
        <span className="text-[12px] font-medium text-text-primary truncate max-w-[120px]">
          {node.role_label || node.label}
        </span>
      </div>
      {node.ip && (
        <div className="text-[10px] text-text-secondary font-mono mt-0.5">{node.ip}</div>
      )}
      {/* Badges */}
      <div className="flex items-center gap-1 mt-1.5">
        {node.is_gateway && (
          <span className="inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded bg-accent/15 text-accent">
            <Router className="w-2.5 h-2.5" /> gw
          </span>
        )}
        {node.is_dns && (
          <span className="inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded bg-info/15 text-info">
            <Globe className="w-2.5 h-2.5" /> DNS
          </span>
        )}
        {node.is_switch && (
          <span className="inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded bg-success/15 text-success">
            <Network className="w-2.5 h-2.5" /> sw
          </span>
        )}
        {node.is_access_point && (
          <span className="inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded bg-accent/15 text-accent">
            <RadioTower className="w-2.5 h-2.5" /> AP
          </span>
        )}
        {monitored && (
          <span className="inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded bg-info/15 text-info">
            <Wifi className="w-2.5 h-2.5" /> wazuh
          </span>
        )}
        {node.finding_count > 0 && (
          <span
            className={cn(
              'inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded font-medium',
              node.finding_count > 2 ? 'bg-danger/15 text-danger' : 'bg-warning/15 text-warning',
            )}
          >
            <AlertTriangle className="w-2.5 h-2.5" /> {node.finding_count}
          </span>
        )}
        {node.open_port_count > 0 && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-surface text-text-secondary border border-border">
            {node.open_port_count} ports
          </span>
        )}
      </div>
    </div>
  );
}

const nodeTypes = { host: HostNode, gateway: GatewayNode, segment: SegmentGroupNode };

// ── Build the graph: dagre top-level + host grid inside groups ──
function buildGraph(
  hosts: ScopeHostNode[],
  segments: ScopeSegment[],
  selectedId: string | null,
  onPromote: (ip: string) => void,
): { nodes: Node[]; edges: Edge[] } {
  const overrideByCidr = new Map(segments.map((s) => [s.cidr, s]));

  // Group hosts by computed segment; null segment → the catch-all group.
  const groups = new Map<string, ScopeHostNode[]>();
  for (const h of hosts) {
    const key = h.segment ?? UNKNOWN_KEY;
    const arr = groups.get(key) ?? [];
    arr.push(h);
    groups.set(key, arr);
  }

  // Gateway: promoted to a dedicated top node only when exactly one is tagged.
  const gateways = hosts.filter((h) => h.is_gateway);
  const singleGateway = gateways.length === 1 ? gateways[0] : null;
  const gatewayHostIds = new Set(gateways.map((g) => g.id));

  // Non-gateway members per group: the gateway is lifted to its own node only
  // when there's a single one; multi/zero-gateway keeps everyone in place.
  const membersOf = (key: string): ScopeHostNode[] =>
    (groups.get(key) ?? []).filter((h) => !(singleGateway && gatewayHostIds.has(h.id)));

  // Stable ordering: known segments by cidr, unknown last.
  const knownCidrs = [...groups.keys()].filter((k) => k !== UNKNOWN_KEY).sort();
  const orderedKeys = [...knownCidrs];
  if (groups.has(UNKNOWN_KEY)) orderedKeys.push(UNKNOWN_KEY);

  // Per-group geometry (skip groups left empty once the gateway is lifted).
  const groupMeta = new Map<
    string,
    { cidr: string; label: string; color: string | null; unknown: boolean; width: number; height: number }
  >();
  let paletteIdx = 0;
  for (const key of orderedKeys) {
    const members = membersOf(key);
    if (members.length === 0) continue;
    const unknown = key === UNKNOWN_KEY;
    const cidr = unknown ? '' : key;
    const override = unknown ? undefined : overrideByCidr.get(key);
    const color = unknown ? null : override?.color || segmentColor(paletteIdx);
    const label = unknown ? 'Unknown network' : override?.label || cidr;
    // Only consume a palette slot for real (colored) segments so reordering
    // the unknown group later wouldn't silently skip a slot.
    if (!unknown) paletteIdx += 1;
    const cols = Math.min(COLS, Math.max(1, members.length));
    const rows = Math.max(1, Math.ceil(members.length / COLS));
    const width = PAD * 2 + cols * COL_W;
    const height = HEADER_H + PAD + rows * ROW_H + PAD;
    groupMeta.set(key, { cidr, label, color, unknown, width, height });
  }
  const renderKeys = orderedKeys.filter((k) => groupMeta.has(k));

  // dagre layout for the top level only (gateway + segment groups).
  const g = new graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 70, ranksep: 110, marginx: 30, marginy: 30 });
  g.setDefaultEdgeLabel(() => ({}));
  if (singleGateway) {
    g.setNode(GATEWAY_LAYOUT_KEY, { width: GATEWAY_W, height: GATEWAY_H });
  }
  for (const key of renderKeys) {
    const meta = groupMeta.get(key)!;
    g.setNode(`seg:${key}`, { width: meta.width, height: meta.height });
    if (singleGateway) g.setEdge(GATEWAY_LAYOUT_KEY, `seg:${key}`);
  }
  dagreLayout(g);

  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Gateway node (top-center). Uses the gateway host's REAL id so selecting it
  // resolves correctly in the detail panel.
  if (singleGateway) {
    const pos = g.node(GATEWAY_LAYOUT_KEY);
    nodes.push({
      id: singleGateway.id,
      type: 'gateway',
      position: { x: pos.x - GATEWAY_W / 2, y: pos.y - GATEWAY_H / 2 },
      data: { node: singleGateway, selected: singleGateway.id === selectedId },
      style: { width: GATEWAY_W, height: GATEWAY_H },
    });
    for (const key of renderKeys) {
      edges.push({
        id: `edge:${key}`,
        source: singleGateway.id,
        target: `seg:${key}`,
        type: 'smoothstep',
        style: { stroke: 'rgb(var(--color-border))', strokeWidth: 1.5 },
      });
    }
  }

  // Segment group (parent) nodes + host children inside.
  for (const key of renderKeys) {
    const meta = groupMeta.get(key)!;
    const pos = g.node(`seg:${key}`);
    const groupX = pos.x - meta.width / 2;
    const groupY = pos.y - meta.height / 2;
    const groupId = `seg:${key}`;

    nodes.push({
      id: groupId,
      type: 'segment',
      position: { x: groupX, y: groupY },
      data: {
        cidr: meta.cidr,
        label: meta.label,
        color: meta.color,
        width: meta.width,
        height: meta.height,
        unknown: meta.unknown,
      },
      style: { width: meta.width, height: meta.height },
      draggable: true,
    });

    membersOf(key).forEach((h, i) => {
      const col = i % COLS;
      const row = Math.floor(i / COLS);
      nodes.push({
        id: h.id,
        type: 'host',
        parentId: groupId,
        extent: 'parent',
        position: { x: PAD + col * COL_W, y: HEADER_H + PAD + row * ROW_H },
        data: { node: h, selected: h.id === selectedId, onPromote },
        draggable: true,
      });
    });
  }

  return { nodes, edges };
}

export function TopologyGraph({
  hosts,
  segments,
  selectedId,
  onSelect,
  onSegmentEdit,
  onPromote,
}: {
  hosts: ScopeHostNode[];
  segments: ScopeSegment[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onSegmentEdit: (cidr: string) => void;
  onPromote: (ip: string) => void;
}) {
  const { nodes: builtNodes, edges: builtEdges } = useMemo(
    () => buildGraph(hosts, segments, selectedId, onPromote),
    [hosts, segments, selectedId, onPromote],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(builtNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(builtEdges);

  // useNodesState/useEdgesState are one-shot initializers (useState under the
  // hood), so prop-driven rebuilds (role/segment edits, selection, deletes)
  // must be pushed back into the store via effect — otherwise the topology
  // only ever reflects the first mount.
  useEffect(() => {
    setNodes(builtNodes);
  }, [builtNodes, setNodes]);
  useEffect(() => {
    setEdges(builtEdges);
  }, [builtEdges, setEdges]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      // Segment group header → open the rename/color editor; never select a host.
      if (node.type === 'segment') {
        const cidr = (node.data as SegmentNodeData).cidr;
        if (cidr) onSegmentEdit(cidr);
        return;
      }
      onSelect(node.id);
    },
    [onSelect, onSegmentEdit],
  );
  const onPaneClick = useCallback(() => onSelect(null), [onSelect]);

  if (hosts.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-text-secondary">
        <Radar className="w-10 h-10 mb-3 opacity-40" />
        <p className="text-sm font-medium">No hosts discovered yet</p>
        <p className="text-xs mt-1 max-w-sm text-center">
          Ask the Recon Operator to scan a subnet, or the SOC Operator to pull Wazuh data —
          hosts and findings will appear here.
        </p>
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      onPaneClick={onPaneClick}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      proOptions={{ hideAttribution: true }}
      nodesDraggable
      minZoom={0.15}
      maxZoom={2}
    >
      <Background color="rgb(var(--color-border))" gap={20} size={1} />
      <Controls
        showInteractive={false}
        className="!bg-surface !border-border !rounded-md !shadow-sm [&>button]:!bg-surface [&>button]:!border-border [&>button]:!text-text-primary"
      />
    </ReactFlow>
  );
}
