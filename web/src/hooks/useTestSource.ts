import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SourceType } from "../components/sources/sourceTypes";

export interface TestSourceResult {
  ok: boolean;
  step: "connect" | "auth" | "mount" | "list" | "config" | null;
  error: string | null;
  // Phase 3a — NFS only. `tier` reports which protocol path proved
  // export validity (mount3 / nfsv4 / tcp). `warn` is non-empty when
  // the cascade fell back to TCP and couldn't validate the export.
  tier?: string | null;
  warn?: string | null;
}

export function useTestSource() {
  return useMutation({
    mutationFn: (data: {
      type: SourceType;
      connection_config: Record<string, unknown>;
      // v0.31.3 — let the probe resolve a credential profile / host the
      // same way a scan does, so profile-backed SMB sources test with
      // their real password instead of an empty inline config.
      credential_profile_id?: string | null;
      host_id?: string | null;
    }) => api.post<TestSourceResult>("/sources/test", data),
  });
}
