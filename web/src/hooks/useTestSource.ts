import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SourceType } from "../components/sources/sourceTypes";

export interface TestSourceResult {
  ok: boolean;
  step: "connect" | "auth" | "mount" | "list" | "config" | null;
  error: string | null;
}

export function useTestSource() {
  return useMutation({
    mutationFn: (data: {
      type: SourceType;
      connection_config: Record<string, unknown>;
    }) => api.post<TestSourceResult>("/sources/test", data),
  });
}
