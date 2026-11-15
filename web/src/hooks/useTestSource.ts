import { useMutation } from "@tanstack/react-query";
import { client, ok } from "../api/client";
import type { components } from "../api/schema";

type S = components["schemas"];

export type TestSourceResult = S["TestResult"];

export function useTestSource() {
  return useMutation({
    // The probe resolves a credential profile / host the same way a scan
    // does, so profile-backed sources test with their real credentials.
    mutationFn: async (data: S["TestSourceRequest"]) =>
      ok(await client.POST("/api/v1/sources/test", { body: data })),
  });
}
