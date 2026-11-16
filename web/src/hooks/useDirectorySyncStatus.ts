import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import type { components } from "../api/schema";

export const DIRECTORY_STATUS_KEY = ["access", "directory-status"] as const;

/** Poll every 2 s while any backend is syncing, otherwise not at all. */
export function pollInterval(
  data: components["schemas"]["DirectoryStatusResponse"] | undefined,
): number | false {
  return data?.backends.some((b) => b.running) ? 2000 : false;
}

/** True on the running -> idle transition: a finished sync changed group data. */
export function syncJustFinished(wasRunning: boolean, running: boolean): boolean {
  return wasRunning && !running;
}

/** Directory sync status, polled while running. A finished sync drops open
 * lookups and searches; lives above the bar so it works while the bar is hidden. */
export function useDirectorySyncStatus() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: DIRECTORY_STATUS_KEY,
    queryFn: async () => ok(await client.GET("/api/v1/access/directory/status")),
    refetchInterval: (q) => pollInterval(q.state.data),
  });
  const running = status.data?.backends.some((b) => b.running) ?? false;
  const wasRunning = useRef(false);
  useEffect(() => {
    if (syncJustFinished(wasRunning.current, running)) {
      qc.invalidateQueries({ queryKey: ["access", "identity"] });
      qc.invalidateQueries({ queryKey: ["access", "identities"] });
    }
    wasRunning.current = running;
  }, [running, qc]);
  return status;
}
