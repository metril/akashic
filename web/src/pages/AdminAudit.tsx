import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Spinner, EmptyState, Page, Select, Input, Button } from "../components/ui";
import type { AuditEventList, AuditEvent } from "../types";

const KNOWN_EVENT_TYPES = [
  "search_as_used",
  "identity_added",
  "identity_removed",
  "binding_added",
  "binding_removed",
  "groups_auto_resolved",
];

const EVENT_TYPE_OPTIONS = [
  { value: "", label: "All" },
  ...KNOWN_EVENT_TYPES.map((t) => ({ value: t, label: t })),
];

export default function AdminAudit() {
  const [eventType, setEventType] = useState("");
  const [fromDate, setFromDate]   = useState("");
  const [toDate, setToDate]       = useState("");
  const [expanded, setExpanded]   = useState<string | null>(null);
  const [page, setPage]           = useState(1);

  const audit = useQuery<AuditEventList>({
    queryKey: ["admin-audit", eventType, fromDate, toDate, page],
    queryFn: () => {
      const p = new URLSearchParams();
      if (eventType) p.set("event_type", eventType);
      if (fromDate)  p.set("from", new Date(fromDate).toISOString());
      if (toDate)    p.set("to", new Date(toDate).toISOString());
      p.set("page", String(page));
      return api.get<AuditEventList>(`/admin/audit?${p.toString()}`);
    },
  });

  const items = audit.data?.items ?? [];
  const totalPages = audit.data
    ? Math.max(1, Math.ceil(audit.data.total / audit.data.page_size))
    : 1;

  return (
    <Page
      title="Audit log"
      description="Recent identity-management and search_as events."
      width="full"
    >
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <Select
          label="Event type"
          value={eventType}
          onChange={(e) => { setEventType(e.target.value); setPage(1); }}
          options={EVENT_TYPE_OPTIONS}
        />
        <Input
          label="From"
          type="datetime-local"
          value={fromDate}
          onChange={(e) => { setFromDate(e.target.value); setPage(1); }}
        />
        <Input
          label="To"
          type="datetime-local"
          value={toDate}
          onChange={(e) => { setToDate(e.target.value); setPage(1); }}
        />
      </div>

      {audit.error && (
        <div
          role="alert"
          className="text-sm text-rose-800 dark:text-rose-200 bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-700/40 rounded px-3 py-2 mb-3"
        >
          {audit.error instanceof Error ? audit.error.message : "Couldn't load audit events."}
        </div>
      )}

      {audit.isLoading ? (
        <div className="flex items-center justify-center py-16 text-fg-subtle">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <div className="border border-line rounded py-12">
          <EmptyState
            title="No events"
            description="Audit events appear here as users act."
          />
        </div>
      ) : (
        <div className="border border-line rounded">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-meta text-fg-muted uppercase border-b border-line">
                <th className="text-left px-3 py-2 font-semibold">Time</th>
                <th className="text-left px-3 py-2 font-semibold">User</th>
                <th className="text-left px-3 py-2 font-semibold">Event</th>
                <th className="text-left px-3 py-2 font-semibold">IP</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <Row key={e.id} event={e} expanded={expanded === e.id}
                     onToggle={() => setExpanded(expanded === e.id ? null : e.id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {audit.data && audit.data.total > audit.data.page_size && (
        <div className="flex items-center justify-between mt-3 text-xs text-fg-muted">
          <div>{audit.data.total} total</div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={page === 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              aria-label="Previous page"
            >
              Prev
            </Button>
            <span aria-live="polite" className="text-fg">
              Page {page} of {totalPages}
            </span>
            <Button
              size="sm"
              variant="secondary"
              disabled={page * audit.data.page_size >= audit.data.total}
              onClick={() => setPage((p) => p + 1)}
              aria-label="Next page"
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </Page>
  );
}

function Row({
  event, expanded, onToggle,
}: { event: AuditEvent; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="border-b border-line-subtle last:border-b-0 hover:bg-surface-muted">
        <td className="px-3 py-1.5 text-fg-muted font-mono text-xs">
          {new Date(event.occurred_at).toLocaleString()}
        </td>
        <td className="px-3 py-1.5 text-fg font-mono text-xs">
          {event.user_id ? event.user_id.slice(0, 8) : "—"}
        </td>
        <td className="px-3 py-1.5 text-fg">{event.event_type}</td>
        <td className="px-3 py-1.5 text-fg-muted font-mono text-xs">{event.request_ip}</td>
        <td className="px-3 py-1.5 text-right">
          <button
            type="button"
            onClick={onToggle}
            aria-label={expanded ? "Collapse event details" : "Expand event details"}
            aria-expanded={expanded}
            className="text-xs text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 rounded px-1"
          >
            {expanded ? "▾" : "▸"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-line-subtle bg-app">
          <td colSpan={5} className="px-3 py-3">
            <pre className="text-xs font-mono text-fg whitespace-pre-wrap break-all max-h-96 overflow-y-auto">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}
