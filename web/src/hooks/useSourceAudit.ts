import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Paginated } from "../types";

export interface AuditEvent {
  id: string;
  user_id: string | null;
  event_type: string;
  occurred_at: string;
  source_id: string | null;
  request_ip: string;
  user_agent: string;
  payload: Record<string, unknown>;
}

/**
 * Per-source audit timeline. Filtered server-side via the
 * `/api/v1/sources/{id}/audit` endpoint that gates on read-access to
 * the source (admin-bypass for admins).
 *
 * `enabled` lets the consumer suspend the query while the drawer's
 * History tab is hidden.
 */
export function useSourceAudit(
  sourceId: string | null | undefined,
  cursor: string | null = null,
  limit: number = 50,
  enabled: boolean = true,
) {
  return useQuery<Paginated<AuditEvent>>({
    queryKey: ["sources", sourceId, "audit", cursor, limit],
    queryFn: () =>
      api.get<Paginated<AuditEvent>>(
        `/sources/${sourceId}/audit?limit=${limit}` +
          (cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""),
      ),
    enabled: enabled && !!sourceId,
  });
}
