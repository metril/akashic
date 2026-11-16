import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../../api/client";
import { useScopes } from "../../hooks/useScopes";
import { Badge, Button } from "../ui";

const LABEL: Record<string, string> = { ad: "AD", authentik: "Authentik" };

/** Directory sync state per backend plus a "Sync now" trigger. */
export function DirectorySyncBar() {
  const qc = useQueryClient();
  const { has } = useScopes();
  const status = useQuery({
    queryKey: ["access", "directory-status"],
    queryFn: async () => ok(await client.GET("/api/v1/access/directory/status")),
    refetchInterval: (q) => (q.state.data?.backends.some((b) => b.running) ? 2000 : false),
  });
  const sync = useMutation({
    mutationFn: async () => ok(await client.POST("/api/v1/access/directory/sync")),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["access", "directory-status"] }),
  });

  const backends = status.data?.backends ?? [];
  const running = backends.some((b) => b.running);
  const canSync = has("maintenance:write");

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium text-fg">Directory</span>
        {status.error && (
          <span className="text-rose-700 dark:text-rose-300">
            {status.error instanceof Error ? status.error.message : "Status failed"}
          </span>
        )}
        {backends.map((b) => (
          <span key={b.backend} className="flex items-center gap-1.5 text-fg-muted">
            <Badge variant={!b.configured ? "offline" : b.running ? "scanning" : b.last_run?.error ? "failed" : "online"}>
              {LABEL[b.backend] ?? b.backend}
            </Badge>
            {!b.configured
              ? "not configured"
              : b.running
                ? "syncing…"
                : b.last_success_at
                  ? `synced ${b.last_success_at}`
                  : "never synced"}
            {b.configured && !b.running && b.last_run?.error && (
              <span className="text-rose-700 dark:text-rose-300">last run failed: {b.last_run.error}</span>
            )}
          </span>
        ))}
        {status.data?.next_run_at && (
          <span className="text-fg-subtle">next run {status.data.next_run_at}</span>
        )}
        <div className="flex-1" />
        {canSync && (
          <Button size="sm" variant="secondary" onClick={() => sync.mutate()} loading={sync.isPending || running}>
            Sync now
          </Button>
        )}
      </div>
      {sync.error && (
        <div role="alert" className="text-sm text-rose-700 dark:text-rose-300">
          {sync.error instanceof Error ? sync.error.message : "Sync failed"}
        </div>
      )}
    </div>
  );
}
