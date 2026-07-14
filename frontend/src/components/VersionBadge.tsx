import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { useUpdateStatus } from '@/hooks/useUpdateStatus';

interface VersionBadgeProps {
  collapsed?: boolean;
}

/**
 * Sidebar version label. Glows when a newer Vigilus release is available;
 * stays muted when current (or when the check hasn't finished / failed).
 * Dismissing the update banner does not turn the glow off.
 */
export function VersionBadge({ collapsed = false }: VersionBadgeProps) {
  const { status } = useUpdateStatus();
  const version = status?.current_version;
  const updateAvailable = Boolean(status?.update_available);

  if (!version) return null;
  // Collapsed sidebar: only show a cue when an update is available.
  if (collapsed && !updateAvailable) return null;

  const label = version.startsWith('v') ? version : `v${version}`;
  const title = updateAvailable
    ? `Update available: ${status?.latest_version ?? 'newer release'}`
    : 'Up to date';

  return (
    <Link
      to="/settings"
      title={title}
      aria-label={title}
      className={cn(
        'flex items-center rounded-md text-[11px] font-mono tracking-wide transition-colors',
        collapsed ? 'justify-center px-0 py-[7px]' : 'px-2.5 py-[7px]',
        updateAvailable
          ? 'text-accent version-glow hover:text-accent-hover'
          : 'text-text-secondary/70 hover:text-text-secondary',
      )}
    >
      {collapsed ? (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent version-glow-dot" />
      ) : (
        <span>{label}</span>
      )}
    </Link>
  );
}
