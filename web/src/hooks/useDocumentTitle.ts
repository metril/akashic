import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Source } from "../types";

const BASE_TITLE = "Akashic";

// Reflect active-scan count in the browser tab title so a user with the
// app in a background tab can sees that work is happening. Counts
// sources currently in `scanning` state — accurate without needing a
// separate /scans poll, because the existing /sources query refreshes
// itself when scans start/stop.
export function useDocumentTitle() {
  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });
  const count = (sourcesQuery.data ?? []).filter(
    (s) => s.status === "scanning",
  ).length;

  useEffect(() => {
    document.title = count > 0 ? `(${count} scanning) ${BASE_TITLE}` : BASE_TITLE;
  }, [count]);
}
