import { memo } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../ui";

interface Props {
  hostId: string | null;
  hostName: string;
  hostType: string | null;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
}

/**
 * Sticky-ish header row that introduces a group of sources sharing a
 * Host. Clicking the chevron collapses / expands the section, with
 * state persisted in localStorage by the page (see
 * `lib/sourcesGrouping.ts`).
 *
 * Renders a link to the Hosts page so the user can jump to credential
 * editing or share discovery in one click. For the "Direct sources"
 * bucket (host_id=null), the link is omitted because there's no host
 * page to land on.
 */
export const HostHeader = memo(function HostHeader({
  hostId, hostName, hostType, count, collapsed, onToggle,
}: Props) {
  return (
    <div
      className="sticky top-0 z-10 bg-app/95 backdrop-blur-sm border-b border-line-subtle px-3 py-1.5 flex items-center gap-2"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-label={collapsed ? "Expand group" : "Collapse group"}
        className="text-fg-muted hover:text-fg p-0.5 rounded"
      >
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2"
          style={{
            transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
            transition: "transform 120ms ease",
          }}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {hostId ? (
        <Link
          to={`/hosts?host=${hostId}`}
          className="text-sm font-semibold text-fg hover:text-blue-600 hover:underline truncate"
        >
          {hostName}
        </Link>
      ) : (
        <span className="text-sm font-semibold text-fg-muted truncate">
          {hostName}
        </span>
      )}
      {hostType && <Badge variant="neutral">{hostType}</Badge>}
      <span className="text-xs text-fg-muted ml-auto">
        {count} {count === 1 ? "share" : "shares"}
      </span>
    </div>
  );
});
