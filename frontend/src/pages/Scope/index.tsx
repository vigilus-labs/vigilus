import { useCallback, useEffect, useState } from 'react';
import { Network, ShieldCheck, Radar, AlertTriangle, Wifi, Table2 } from 'lucide-react';
import { api } from '@/lib/api';
import { useToast } from '@/components/Notifications';
import type {
  ScopeOverview,
  ScopeHostNode,
  ScopeTimeseriesPoint,
  ScopeSeverityBucket,
  ScopePortBucket,
  ScopeInventoryHost,
  ScopeSegment,
} from '@/types';
import { cn } from '@/lib/utils';
import { TopologyGraph } from './TopologyGraph';
import { InventoryTable } from './InventoryTable';
import { NodeDetail } from './NodeDetail';
import { SegmentEditor } from './SegmentEditor';
import { AlertsOverTime } from './charts/AlertsOverTime';
import { SeverityDonut } from './charts/SeverityDonut';
import { PortsDistribution } from './charts/PortsDistribution';
import { InventoryBar } from './charts/InventoryBar';

function StatTile({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: number | string;
  color: string;
}) {
  return (
    <div className="bg-white dark:bg-surface border border-border rounded-card p-4 flex items-center gap-3">
      <div className={cn('w-9 h-9 rounded-full flex items-center justify-center shrink-0', color)}>
        <Icon className="w-4 h-4" />
      </div>
      <div>
        <div className="text-xl font-semibold text-text-primary leading-none">{value}</div>
        <div className="text-[11px] text-text-secondary mt-1">{label}</div>
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon: Icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ElementType;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-md font-medium transition-colors',
        active
          ? 'bg-accent/15 text-accent'
          : 'text-text-secondary hover:text-text-primary hover:bg-surface',
      )}
    >
      <Icon className="w-4 h-4" />
      {label}
    </button>
  );
}

export default function Scope() {
  const toast = useToast();
  const [overview, setOverview] = useState<ScopeOverview | null>(null);
  const [hosts, setHosts] = useState<ScopeHostNode[]>([]);
  const [timeseries, setTimeseries] = useState<ScopeTimeseriesPoint[]>([]);
  const [severity, setSeverity] = useState<ScopeSeverityBucket[]>([]);
  const [ports, setPorts] = useState<ScopePortBucket[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<'topology' | 'inventory'>('topology');
  const [inventory, setInventory] = useState<ScopeInventoryHost[]>([]);
  const [inventoryLoading, setInventoryLoading] = useState(false);
  const [inventoryLoaded, setInventoryLoaded] = useState(false);
  const [segments, setSegments] = useState<ScopeSegment[]>([]);
  const [editingCidr, setEditingCidr] = useState<string | null>(null);

  const loadInventory = useCallback(() => {
    setInventoryLoading(true);
    api
      .scopeInventory()
      .then((rows) => {
        setInventory(rows);
        setInventoryLoaded(true);
      })
      .finally(() => setInventoryLoading(false));
  }, []);

  useEffect(() => {
    Promise.all([
      api.scopeOverview(),
      api.scopeHosts(),
      api.scopeFindingsTimeseries(30),
      api.scopeFindingsSeverity(),
      api.scopePortsDistribution(),
      api.scopeSegments(),
    ])
      .then(([o, h, ts, sev, p, segs]) => {
        setOverview(o);
        setHosts(h);
        setTimeseries(ts);
        setSeverity(sev);
        setPorts(p);
        setSegments(segs);
      })
      .finally(() => setLoading(false));
  }, []);

  // Lazy-load the inventory the first time the table tab is opened.
  useEffect(() => {
    if (view === 'inventory' && !inventoryLoaded && !inventoryLoading) {
      loadInventory();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // After deletes: refresh inventory plus the topology/overview/charts whose
  // counts the deletion changed.
  const handleDeleted = useCallback(() => {
    loadInventory();
    setSelectedId(null);
    api.scopeOverview().then(setOverview);
    api.scopeHosts().then(setHosts);
    api.scopeFindingsSeverity().then(setSeverity);
    api.scopePortsDistribution().then(setPorts);
  }, [loadInventory]);

  // After a role/segment edit: refresh the topology hosts (role flags +
  // segment overrides drive the diagram) without touching other tabs.
  const handleRoleOrSegmentChanged = useCallback(() => {
    api.scopeHosts().then(setHosts);
    api.scopeSegments().then(setSegments);
  }, []);

  // "+" button on an unmanaged topology node: promote that single host.
  const handlePromote = useCallback(
    async (ip: string) => {
      try {
        const res = await api.scopePromoteHosts([ip]);
        if (res.created.length) {
          toast(`Added ${ip} to Server Inventory`, 'success');
        } else if (res.already_managed.length) {
          toast(`${ip} is already managed`, 'info');
        } else {
          toast(`Couldn't add ${ip}`, 'error');
        }
        handleDeleted();
      } catch (e) {
        toast(e instanceof Error ? e.message : 'Add to inventory failed', 'error');
      }
    },
    [toast, handleDeleted],
  );

  const selectedHost = hosts.find((h) => h.id === selectedId) ?? undefined;

  return (
    <div className="h-full flex flex-col">
      {/* Header + stat tiles */}
      <div className="px-6 pt-6 pb-3 shrink-0">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-2xl font-medium text-text-primary mb-1">Scope</h1>
            <p className="text-text-secondary text-sm">
              A single pane of glass over your network — managed inventory, discovered hosts, and live telemetry.
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatTile icon={ShieldCheck} label="Managed" value={overview?.managed ?? 0} color="bg-accent/15 text-accent" />
          <StatTile icon={Radar} label="Discovered" value={overview?.discovered_unique ?? 0} color="bg-info/15 text-info" />
          <StatTile icon={Network} label="Unmanaged" value={overview?.unmanaged ?? 0} color="bg-warning/15 text-warning" />
          <StatTile icon={AlertTriangle} label="Findings" value={overview?.findings ?? 0} color="bg-danger/15 text-danger" />
          <StatTile icon={Wifi} label="Open Ports" value={overview?.open_ports ?? 0} color="bg-surface text-text-secondary border border-border" />
        </div>
      </div>

      {/* Body: topology (hero) + charts column, with optional detail panel */}
      <div className="flex-1 min-h-0 flex gap-4 px-6 pb-6">
        <div className="flex-1 min-w-0 flex gap-4">
          <div className="flex-1 min-w-0 bg-surface border border-border rounded-card overflow-hidden flex flex-col">
            <div className="px-2 py-1.5 border-b border-border flex items-center gap-1">
              <TabButton
                active={view === 'topology'}
                onClick={() => setView('topology')}
                icon={Network}
                label="Topology"
              />
              <TabButton
                active={view === 'inventory'}
                onClick={() => setView('inventory')}
                icon={Table2}
                label="Inventory"
              />
              <span className="text-[11px] text-text-secondary ml-auto pr-2">
                {view === 'topology'
                  ? `Click a node for details · ${hosts.length} hosts`
                  : 'Select hosts to delete from inventory'}
              </span>
            </div>
            <div className="flex-1 min-h-0">
              {view === 'topology' ? (
                loading ? (
                  <div className="h-full flex items-center justify-center text-text-secondary text-sm">Loading scope…</div>
                ) : (
                  <TopologyGraph
                    hosts={hosts}
                    segments={segments}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    onSegmentEdit={(cidr) => setEditingCidr(cidr)}
                    onPromote={handlePromote}
                  />
                )
              ) : (
                <InventoryTable
                  hosts={inventory}
                  loading={inventoryLoading}
                  onSelect={setSelectedId}
                  onDeleted={handleDeleted}
                  onPromoted={handleDeleted}
                />
              )}
            </div>
          </div>

          {/* Charts column */}
          <div className="w-[340px] shrink-0 space-y-3 overflow-y-auto">
            <AlertsOverTime data={timeseries} />
            <SeverityDonut data={severity} />
            <PortsDistribution data={ports} />
            <InventoryBar overview={overview} />
          </div>
        </div>

        {/* Detail side panel */}
        {selectedId && (
          <NodeDetail
            identity={selectedId}
            hostNode={selectedHost}
            onClose={() => setSelectedId(null)}
            onRoleChanged={handleRoleOrSegmentChanged}
          />
        )}
      </div>

      {/* Segment label/color editor */}
      {editingCidr && (
        <SegmentEditor
          cidr={editingCidr}
          initial={segments.find((s) => s.cidr === editingCidr)}
          onClose={() => setEditingCidr(null)}
          onSaved={handleRoleOrSegmentChanged}
        />
      )}
    </div>
  );
}
