import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { client, ok } from "../../api/client";
import { Badge, Input, Select, Spinner } from "../ui";
import type { components } from "../../api/schema";

type S = components["schemas"];
type IdentityResult = S["IdentityResult"];
type BackendStatus = S["BackendStatus"];

const BACKEND_LABEL: Record<string, string> = { ad: "AD", authentik: "Authentik", akashic: "akashic" };

const KIND_OPTIONS = [
  { value: "", label: "People and groups" },
  { value: "person", label: "People" },
  { value: "group", label: "Groups" },
];

export function IdentityResultRow({
  result,
  onPick,
  selected,
}: {
  result: IdentityResult;
  onPick: (id: string) => void;
  selected?: boolean;
}) {
  const label = result.display_name ?? result.username ?? result.id;
  return (
    <button
      type="button"
      onClick={() => onPick(result.id)}
      aria-current={selected ? "true" : undefined}
      className={
        "w-full text-left px-3 py-2 flex flex-wrap items-center gap-2 text-sm hover:bg-surface-muted focus:outline-none focus:ring-2 focus:ring-accent-500 " +
        (selected ? "bg-accent-50 dark:bg-accent-500/15" : "")
      }
    >
      <span className="font-medium text-fg">{label}</span>
      {result.username && result.username !== label && (
        <span className="text-fg-muted">{result.username}</span>
      )}
      {result.email && <span className="text-fg-subtle">{result.email}</span>}
      <Badge variant={result.kind === "group" ? "info" : "online"}>{result.kind}</Badge>
      {result.backends.map((b) => (
        <Badge key={`${b.backend}:${b.ref}`} variant="neutral">
          {BACKEND_LABEL[b.backend] ?? b.backend}
        </Badge>
      ))}
      {result.match_quality === "possible" && (
        <Badge variant="scanning" title="Only the username matches another result; not merged.">
          possible match
        </Badge>
      )}
      {!result.has_fs_identity && (
        <Badge variant="failed" title="No SID, POSIX id or binding to look up files with.">
          no file-system identity
        </Badge>
      )}
    </button>
  );
}

export function BackendStatusChips({ statuses }: { statuses: BackendStatus[] }) {
  return (
    <div className="flex flex-wrap gap-2 text-xs">
      {statuses.map((s) => (
        <Badge
          key={s.backend}
          variant={s.status === "ok" ? "online" : s.status === "unavailable" ? "failed" : "offline"}
        >
          {BACKEND_LABEL[s.backend] ?? s.backend}
          {s.status === "not_configured" && ": not configured"}
          {s.status === "unavailable" && `: unavailable${s.detail ? ` (${s.detail})` : ""}`}
        </Badge>
      ))}
    </div>
  );
}

/** Text for the search status line. `typed` is the input, `debounced` the
 * query the results belong to; while they differ a new search is pending. */
export function searchStatus(s: {
  typed: string;
  debounced: string;
  fetching: boolean;
  failed: boolean;
  count: number | null;
}): string {
  if (!s.typed) return "";
  if (s.typed !== s.debounced || s.fetching) return "Searching…";
  if (s.failed) return "Search failed";
  if (s.count === null) return "";
  return s.count ? `${s.count} results` : "No people or groups match";
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Type-ahead search over people and groups from AD, Authentik and Akashic users. */
export function IdentityPicker({ onPick }: { onPick: (id: string) => void }) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const typed = text.trim();
  const q = useDebounced(typed, 250);

  const search = useQuery({
    queryKey: ["access", "identities", q, kind],
    queryFn: async () =>
      ok(
        await client.GET("/api/v1/access/identities", {
          params: { query: { q, ...(kind ? { kind: kind as "person" | "group" } : {}), limit: 20 } },
        }),
      ),
    enabled: q.length > 0,
  });

  const results = search.data?.results ?? [];
  const status = searchStatus({
    typed,
    debounced: q,
    fetching: search.isFetching,
    failed: Boolean(search.error),
    count: search.data ? results.length : null,
  });

  return (
    <div className="space-y-3">
      <div className="flex items-end gap-3">
        <div className="flex-1">
          <Input
            label="Search people and groups"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Name, username, email…"
          />
        </div>
        <div className="w-48">
          <Select
            label="Kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            options={KIND_OPTIONS}
          />
        </div>
      </div>
      <div role="status" aria-live="polite" className="text-xs text-fg-muted flex items-center gap-2">
        {status === "Searching…" && <Spinner />}
        {status}
      </div>
      {search.error && (
        <div className="border border-rose-300 bg-rose-50 dark:bg-rose-950/30 dark:border-rose-700/40 rounded p-3 text-sm text-rose-800 dark:text-rose-200">
          {search.error instanceof Error ? search.error.message : "Search failed"}
        </div>
      )}
      {search.data && (
        <>
          <BackendStatusChips statuses={search.data.backends_status} />
          {results.length > 0 && (
            <ul className="divide-y divide-line border border-line rounded-lg">
              {results.map((r) => (
                <li key={r.id}>
                  <IdentityResultRow
                    result={r}
                    selected={r.id === picked}
                    onPick={(id) => {
                      setPicked(id);
                      onPick(id);
                    }}
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
