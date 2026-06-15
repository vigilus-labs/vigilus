import { useMemo, useCallback } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type NodeProps,
  Handle,
  Position,
  useNodesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ShieldCheck, Radar, AlertTriangle, Wifi } from 'lucide-react';
import type { ScopeHostNode } from '@/types';
import { cn } from '@/lib/utils';

type ScopeNodeData = {
  node: ScopeHostNode;
  selected: boolean;
};

/** A single host node on the topology. Styled by origin + telemetry badges. */
function HostNode({ data }: NodeProps) {
  const { node, selected } = data as ScopeNodeData;
  const managed = node.origins.includes('managed');
  const discovered = node.origins.includes('discovered');
  const monitored = node.monitored;

  return (
    <div
      className={cn(
        'relative px-3 py-2 rounded-lg border-2 bg-white dark:bg-surface min-w-[140px] transition-shadow',
        selected ? 'ring-2 ring-accent shadow-md' : 'shadow-sm',
        // Origin rings
        managed && discovered
          ? 'border-accent'
          : managed
            ? 'border-accent/70'
            : 'border-border border-dashed',
      )}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div className="flex items-center gap-1.5">
        {managed ? (
          <ShieldCheck className="w-3.5 h-3.5 text-accent shrink-0" />
        ) : (
          <Radar className="w-3.5 h-3.5 text-text-secondary shrink-0" />
        )}
        <span className="text-[12px] font-medium text-text-primary truncate max-w-[120px]">
          {node.label}
        </span>
      </div>
      {node.ip && (
        <div className="text-[10px] text-text-secondary font-mono mt-0.5">{node.ip}</div>
      )}
      {/* Badges */}
      <div className="flex items-center gap-1 mt-1.5">
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
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { host: HostNode };

/** Build react-flow nodes laid out on a grid grouped by /24 subnet. */
function buildNodes(hosts: ScopeHostNode[], selectedId: string | null): Node[] {
  // Group by subnet (/24) so related hosts cluster visually.
  const groups = new Map<string, ScopeHostNode[]>();
  for (const h of hosts) {
    const ip = h.ip || '';
    const subnet = /^\d+\.\d+\.\d+/.test(ip) ? ip.split('.').slice(0, 3).join('.') : 'other';
    const arr = groups.get(subnet) ?? [];
    arr.push(h);
    groups.set(subnet, arr);
  }

  const COLS = 3;
  const COL_W = 220;
  const ROW_H = 140;
  const GROUP_GAP_X = 80;
  const GROUP_GAP_Y = 60;

  const nodes: Node[] = [];
  let groupIndex = 0;
  const subnets = [...groups.keys()].sort();
  for (const subnet of subnets) {
    const members = groups.get(subnet)!;
    const gx = (groupIndex % 2) * (COLS * COL_W + GROUP_GAP_X);
    const gy = Math.floor(groupIndex / 2) * (Math.ceil(members.length / COLS) * ROW_H + GROUP_GAP_Y);
    members.forEach((h, i) => {
      nodes.push({
        id: h.id,
        type: 'host',
        position: { x: gx + (i % COLS) * COL_W, y: gy + Math.floor(i / COLS) * ROW_H },
        data: { node: h, selected: h.id === selectedId },
      });
    });
    groupIndex += 1;
  }
  return nodes;
}

export function TopologyGraph({
  hosts,
  selectedId,
  onSelect,
}: {
  hosts: ScopeHostNode[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const initial = useMemo(() => buildNodes(hosts, selectedId), [hosts, selectedId]);
  const [nodes, , onNodesChange] = useNodesState(initial);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => onSelect(node.id),
    [onSelect],
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
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={onNodeClick}
      onPaneClick={onPaneClick}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      proOptions={{ hideAttribution: true }}
      nodesDraggable
      minZoom={0.2}
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
