import { useQuery } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import type { components } from "../api/schema";


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
  return useQuery<components["schemas"]["Page_AuditEventOut_"]>({
    queryKey: ["sources", sourceId, "audit", cursor, limit],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/sources/{source_id}/audit", {
          params: { path: { source_id: sourceId! }, query: { limit, cursor } },
        }),
      ),
    enabled: enabled && !!sourceId,
  });
}
