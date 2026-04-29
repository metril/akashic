import { useState } from "react";
import { useSourceAudit, type AuditEvent } from "../../hooks/useSourceAudit";
import { formatDateTime } from "../../lib/format";

interface SourceAuditTabProps {
  sourceId: string;
  visible: boolean;
}

const EVENT_LABEL: Record<string, string> = {
  source_created: "Created",
  source_updated: "Updated",
  source_deleted: "Deleted",
  source_test_run: "Connection test",
  binding_added: "Binding added",
  binding_removed: "Binding removed",
};

const EVENT_COLOR: Record<string, string> = {
  source_created: "bg-emerald-100 text-emerald-800",
  source_updated: "bg-blue-100 text-blue-800",
  source_deleted: "bg-rose-100 text-rose-800",
  source_test_run: "bg-gray-100 text-gray-700",
};

export function SourceAuditTab({ sourceId, visible }: SourceAuditTabProps) {
  const { data, isLoading, error } = useSourceAudit(sourceId, 1, 50, visible);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!visible) return null;
  if (isLoading) {
    return <p className="text-sm text-gray-500">Loading history…</p>;
  }
  if (error) {
    return (
      <p className="text-sm text-rose-600">
        {error instanceof Error ? error.message : "Failed to load audit history"}
      </p>
    );
  }
  const items = data?.items ?? [];
  if (items.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No history yet. Edits, scans, and config changes will appear here.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {items.map((evt) => (
        <AuditRow
          key={evt.id}
          event={evt}
          expanded={expandedId === evt.id}
          onToggle={() => setExpandedId(expandedId === evt.id ? null : evt.id)}
        />
      ))}
    </ul>
  );
}

function AuditRow({
  event,
  expanded,
  onToggle,
}: {
  event: AuditEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
  const label = EVENT_LABEL[event.event_type] ?? event.event_type;
  const colorClass = EVENT_COLOR[event.event_type] ?? "bg-gray-100 text-gray-700";

  return (
    <li className="border border-gray-200 rounded-md overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-50"
      >
        <span
          className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}
        >
          {label}
        </span>
        <span className="flex-1 min-w-0 text-xs text-gray-700 truncate">
          {summaryFor(event)}
        </span>
        <span className="shrink-0 text-xs text-gray-400">
          {formatDateTime(event.occurred_at)}
        </span>
      </button>
      {expanded && (
        <div className="bg-gray-50 border-t border-gray-200 px-3 py-2 text-xs">
          <DiffOrPayload event={event} />
        </div>
      )}
    </li>
  );
}

function summaryFor(event: AuditEvent): string {
  if (event.event_type === "source_updated") {
    const diff = (event.payload as { diff?: Record<string, unknown> }).diff;
    if (diff && typeof diff === "object") {
      const fields = Object.keys(diff);
      if (fields.length === 0) return "no fields changed";
      return `${fields.length} field${fields.length === 1 ? "" : "s"} changed: ${fields.join(", ")}`;
    }
  }
  if (event.event_type === "source_created") {
    const name = (event.payload as { name?: string }).name;
    return name ? `Created as "${name}"` : "Created";
  }
  if (event.event_type === "source_deleted") {
    return "Source deleted";
  }
  return "";
}

function DiffOrPayload({ event }: { event: AuditEvent }) {
  if (event.event_type === "source_updated") {
    const diff = (event.payload as { diff?: Record<string, unknown> }).diff;
    if (diff && typeof diff === "object") {
      return <FieldDiff diff={diff as Record<string, unknown>} />;
    }
  }
  return (
    <pre className="font-mono text-[11px] text-gray-700 whitespace-pre-wrap break-all">
      {JSON.stringify(event.payload, null, 2)}
    </pre>
  );
}

function FieldDiff({ diff }: { diff: Record<string, unknown> }) {
  return (
    <dl className="space-y-1">
      {Object.entries(diff).map(([field, change]) => {
        // connection_config is a nested {key: {before, after}} dict.
        if (field === "connection_config" && change && typeof change === "object") {
          return (
            <div key={field}>
              <dt className="font-medium text-gray-700">connection_config</dt>
              <dd className="ml-3">
                <FieldDiff diff={change as Record<string, unknown>} />
              </dd>
            </div>
          );
        }
        if (
          change &&
          typeof change === "object" &&
          "before" in change &&
          "after" in change
        ) {
          const c = change as { before: unknown; after: unknown };
          return (
            <div key={field} className="flex flex-wrap items-baseline gap-2">
              <dt className="font-medium text-gray-700">{field}</dt>
              <dd className="text-rose-700 line-through">{format(c.before)}</dd>
              <span className="text-gray-400">→</span>
              <dd className="text-emerald-700">{format(c.after)}</dd>
            </div>
          );
        }
        return (
          <div key={field}>
            <dt className="font-medium text-gray-700">{field}</dt>
            <dd>{JSON.stringify(change)}</dd>
          </div>
        );
      })}
    </dl>
  );
}

function format(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}
