import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Spinner, EmptyState } from "../components/ui";
import type { AuditEventList, AuditEvent } from "../types";

const KNOWN_EVENT_TYPES = [
  "search_as_used",
  "identity_added",
  "identity_removed",
  "binding_added",
  "binding_removed",
  "groups_auto_resolved",
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

  return (
    <div className="px-8 py-7 max-w-5xl">
      <h1 className="text-2xl font-semibold text-gray-900 tracking-tight mb-1">Audit log</h1>
      <p className="text-sm text-gray-500 mb-6">
        Recent identity-management and search_as events.
      </p>

      <div className="flex flex-wrap gap-3 mb-4 text-xs">
        <label className="text-gray-500 flex flex-col">
          Event type
          <select
            value={eventType} onChange={(e) => { setEventType(e.target.value); setPage(1); }}
            className="mt-1 border border-gray-200 rounded px-2 py-1 text-sm"
          >
            <option value="">All</option>
            {KNOWN_EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="text-gray-500 flex flex-col">
          From
          <input
            type="datetime-local" value={fromDate}
            onChange={(e) => { setFromDate(e.target.value); setPage(1); }}
            className="mt-1 border border-gray-200 rounded px-2 py-1 text-sm"
          />
        </label>
        <label className="text-gray-500 flex flex-col">
          To
          <input
            type="datetime-local" value={toDate}
            onChange={(e) => { setToDate(e.target.value); setPage(1); }}
            className="mt-1 border border-gray-200 rounded px-2 py-1 text-sm"
          />
        </label>
      </div>

      {audit.error && (
        <div className="text-sm text-rose-600 bg-rose-50 rounded px-3 py-2 mb-3">
          {audit.error instanceof Error ? audit.error.message : "Error"}
        </div>
      )}

      {audit.isLoading ? (
        <div className="flex items-center justify-center py-16 text-gray-400">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <div className="border border-gray-200 rounded py-12">
          <EmptyState
            title="No events"
            description="Audit events appear here as users act."
          />
        </div>
      ) : (
        <div className="border border-gray-200 rounded">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-200">
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
        <div className="flex items-center justify-between mt-3 text-xs text-gray-500">
          <div>{audit.data.total} total</div>
          <div className="flex gap-2">
            <button
              type="button" disabled={page === 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="border border-gray-200 rounded px-2 py-1 disabled:opacity-50 hover:bg-gray-50"
            >Prev</button>
            <button
              type="button"
              disabled={page * audit.data.page_size >= audit.data.total}
              onClick={() => setPage((p) => p + 1)}
              className="border border-gray-200 rounded px-2 py-1 disabled:opacity-50 hover:bg-gray-50"
            >Next</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({
  event, expanded, onToggle,
}: { event: AuditEvent; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="border-b border-gray-100 last:border-b-0 hover:bg-gray-50">
        <td className="px-3 py-1.5 text-gray-600 font-mono text-xs">
          {new Date(event.occurred_at).toLocaleString()}
        </td>
        <td className="px-3 py-1.5 text-gray-700 font-mono text-xs">
          {event.user_id ? event.user_id.slice(0, 8) : "—"}
        </td>
        <td className="px-3 py-1.5 text-gray-800">{event.event_type}</td>
        <td className="px-3 py-1.5 text-gray-500 font-mono text-xs">{event.request_ip}</td>
        <td className="px-3 py-1.5 text-right">
          <button
            type="button" onClick={onToggle}
            className="text-xs text-gray-500 hover:text-gray-800"
          >{expanded ? "▾" : "▸"}</button>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-gray-100 bg-gray-50">
          <td colSpan={5} className="px-3 py-3">
            <pre className="text-xs font-mono text-gray-700 whitespace-pre-wrap break-all">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}
