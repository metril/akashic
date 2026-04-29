import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

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

export interface AuditEventList {
  items: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * Per-source audit timeline. Filtered server-side via the
 * `/api/sources/{id}/audit` endpoint that gates on read-access to
 * the source (admin-bypass for admins).
 *
 * `enabled` lets the consumer suspend the query while the drawer's
 * History tab is hidden.
 */
export function useSourceAudit(
  sourceId: string | null | undefined,
  page: number = 1,
  pageSize: number = 50,
  enabled: boolean = true,
) {
  return useQuery<AuditEventList>({
    queryKey: ["sources", sourceId, "audit", page, pageSize],
    queryFn: () =>
      api.get<AuditEventList>(
        `/sources/${sourceId}/audit?page=${page}&page_size=${pageSize}`,
      ),
    enabled: enabled && !!sourceId,
  });
}
