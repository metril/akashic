/**
 * Storage by mirror-link role for one backing source. The picker lists
 * backing sources (local/nfs/smb/s3) named in an Immich/Paperless source's `mirrors`.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { client, ok } from "../../api/client";
import { useMirroredBy, useSources } from "../../hooks/useSources";
import type { StorageByLinkRole } from "../../api/types";
import { formatBytes, formatNumber } from "../../lib/format";
import { Card, CardHeader, EmptyState, Select, Skeleton, Table } from "../ui";

export function LinkRoleBreakdown() {
  const sourcesQ = useSources();
  const mirroredBy = useMirroredBy();
  const mirrored = useMemo(
    () => (sourcesQ.data ?? []).filter((s) => mirroredBy.has(s.id)),
    [sourcesQ.data, mirroredBy],
  );

  const [picked, setPicked] = useState("");
  const sourceId = mirrored.some((s) => s.id === picked) ? picked : (mirrored[0]?.id ?? "");

  const q = useQuery<StorageByLinkRole[]>({
    queryKey: ["analytics", "storage-by-link-role", sourceId],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/analytics/storage-by-link-role", {
          params: { query: { source_id: sourceId } },
        }),
      ),
    enabled: !!sourceId,
  });

  if (mirrored.length === 0) return null;

  return (
    <Card padding="md" className="mb-5">
      <CardHeader
        title="Storage by link role"
        description="Backing-file bytes grouped by the Immich/Paperless role they serve."
      />
      <div className="w-56 mb-3">
        <Select
          label="Backing source"
          value={sourceId}
          onChange={(e) => setPicked(e.target.value)}
          options={mirrored.map((s) => ({ value: s.id, label: s.name }))}
        />
      </div>
      {q.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : (q.data ?? []).length === 0 ? (
        <EmptyState title="No data" />
      ) : (
        <Table
          data={q.data ?? []}
          rowKey={(r) => r.link_role}
          columns={[
            { key: "role", header: "Role", render: (r) => r.link_role },
            { key: "count", header: "Files", render: (r) => formatNumber(r.count), className: "text-right tabular-nums" },
            { key: "size", header: "Size", render: (r) => formatBytes(r.total_size), className: "text-right tabular-nums" },
          ]}
        />
      )}
    </Card>
  );
}
