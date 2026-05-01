import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Card, Drawer, EmptyState, Page, Spinner } from "../components/ui";
import { EntryDetail } from "../components/EntryDetail";
import { PrincipalLookup } from "../components/access/PrincipalLookup";
import {
  FileToPrincipalsResults,
  PrincipalToFilesResults,
} from "../components/access/AccessResults";

type Mode = "principal" | "file";

interface PrincipalResult {
  principal: { token: string; name: string | null; domain: string | null; kind: string };
  right: string;
  summary: { file_count: number; total_size_bytes: number; source_count: number };
  by_source: { source_id: string; source_name: string; file_count: number }[];
  sample: SampleHit[];
  next_offset: number | null;
}

interface SampleHit {
  id: string;
  source_id: string;
  path: string;
  filename: string;
  size_bytes: number | null;
  owner_name?: string | null;
  fs_modified_at?: number | null;
}

interface FileResult {
  entry_id: string;
  path: string;
  filename: string;
  right: string;
  principals: {
    token: string;
    name?: string | null;
    domain?: string | null;
    kind: string;
    source: string;
  }[];
}

/** Admin "blast radius" page — answers two complementary questions:
 * "what files can principal X reach?" and "who can reach file Y?".
 *
 * Both questions share the same /api/access endpoint (one query param
 * each) so the toggle on this page is purely a frontend affordance.
 *
 * Phase 5 will cycle the same query into the personalized Browse
 * filter — the logged-in user's own SID set becomes the principal
 * argument. This page is the admin-facing surface that pays off the
 * SID-resolution work twice. */
export default function AdminAccess() {
  const [mode, setMode] = useState<Mode>("principal");
  const [right, setRight] = useState<"read" | "write" | "delete">("read");
  const [token, setToken] = useState<string | null>(null);
  const [fileId, setFileId] = useState<string | null>(null);
  const [openEntryId, setOpenEntryId] = useState<string | null>(null);

  const principalQ = useQuery<PrincipalResult>({
    queryKey: ["access", "principal", token, right],
    queryFn: () =>
      api.get<PrincipalResult>(
        `/access?principal=${encodeURIComponent(token!)}&right=${right}&limit=20`,
      ),
    enabled: mode === "principal" && Boolean(token),
  });

  const fileQ = useQuery<FileResult>({
    queryKey: ["access", "file", fileId, right],
    queryFn: () =>
      api.get<FileResult>(`/access?file=${fileId}&right=${right}`),
    enabled: mode === "file" && Boolean(fileId),
  });

  return (
    <Page
      title="Access"
      description="What can a principal reach? Who can reach a file? Admin-only."
      width="wide"
    >
      <div className="flex items-center gap-3 mb-5">
        <ModeToggle mode={mode} onChange={setMode} />
        <div className="flex-1" />
        <RightToggle right={right} onChange={setRight} />
      </div>

      {mode === "principal" ? (
        <Card padding="md" className="mb-5">
          <PrincipalLookup
            onLookup={setToken}
            pending={principalQ.isFetching && Boolean(token)}
          />
        </Card>
      ) : (
        <Card padding="md" className="mb-5">
          <FileLookup
            value={fileId ?? ""}
            onLookup={setFileId}
            pending={fileQ.isFetching && Boolean(fileId)}
          />
        </Card>
      )}

      {mode === "principal" ? (
        token == null ? (
          <EmptyState
            title="Look up a principal"
            description="Pick a kind, type the value, hit Look up. Wildcard kinds (Anyone, Authenticated users) take no value."
          />
        ) : principalQ.isLoading ? (
          <div className="flex justify-center py-12 text-fg-subtle"><Spinner /></div>
        ) : principalQ.error ? (
          <ErrorBanner error={principalQ.error} />
        ) : principalQ.data ? (
          <PrincipalToFilesResults
            data={principalQ.data}
            onSelectEntry={setOpenEntryId}
          />
        ) : null
      ) : fileId == null ? (
        <EmptyState
          title="Paste an entry ID"
          description="Copy an entry's UUID from Browse or Search and paste it above. Returns who has the selected right against that file."
        />
      ) : fileQ.isLoading ? (
        <div className="flex justify-center py-12 text-fg-subtle"><Spinner /></div>
      ) : fileQ.error ? (
        <ErrorBanner error={fileQ.error} />
      ) : fileQ.data ? (
        <FileToPrincipalsResults
          data={fileQ.data}
          onSelectEntry={() => setOpenEntryId(fileId)}
        />
      ) : null}

      <Drawer
        open={Boolean(openEntryId)}
        onClose={() => setOpenEntryId(null)}
        title="Entry detail"
        width="lg"
      >
        <EntryDetail entryId={openEntryId} />
      </Drawer>
    </Page>
  );
}

function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div role="radiogroup" className="inline-flex rounded-lg border border-line p-0.5 bg-surface">
      {(["principal", "file"] as const).map((m) => (
        <button
          key={m}
          type="button"
          role="radio"
          aria-checked={mode === m}
          onClick={() => onChange(m)}
          className={
            "px-3 py-1.5 text-sm rounded-md transition-colors " +
            (mode === m
              ? "bg-accent-100 text-accent-800 dark:bg-accent-500/20 dark:text-accent-200 font-medium"
              : "text-fg-muted hover:text-fg")
          }
        >
          {m === "principal" ? "Principal → files" : "File → principals"}
        </button>
      ))}
    </div>
  );
}

function RightToggle({
  right,
  onChange,
}: {
  right: "read" | "write" | "delete";
  onChange: (r: "read" | "write" | "delete") => void;
}) {
  return (
    <div role="radiogroup" className="inline-flex rounded-lg border border-line p-0.5 bg-surface text-sm">
      {(["read", "write", "delete"] as const).map((r) => (
        <button
          key={r}
          type="button"
          role="radio"
          aria-checked={right === r}
          onClick={() => onChange(r)}
          className={
            "px-3 py-1 rounded-md transition-colors " +
            (right === r
              ? "bg-accent-100 text-accent-800 dark:bg-accent-500/20 dark:text-accent-200 font-medium"
              : "text-fg-muted hover:text-fg")
          }
        >
          {r}
        </button>
      ))}
    </div>
  );
}

function FileLookup({
  value,
  onLookup,
  pending,
}: {
  value: string;
  onLookup: (id: string) => void;
  pending?: boolean;
}) {
  const [input, setInput] = useState(value);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (trimmed) onLookup(trimmed);
  };
  return (
    <form onSubmit={submit} className="flex items-end gap-3">
      <div className="flex-1">
        <label className="block text-xs font-medium text-fg-muted mb-1.5">
          Entry ID (UUID)
        </label>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="11111111-2222-3333-4444-555555555555"
          className="w-full h-10 px-3 rounded-lg border border-line bg-surface font-mono text-sm focus:outline-none focus:ring-2 focus:ring-accent-500"
        />
      </div>
      <button
        type="submit"
        disabled={!input.trim() || pending}
        className="h-10 px-4 rounded-lg bg-accent-600 text-white text-sm font-medium hover:bg-accent-700 disabled:opacity-50"
      >
        {pending ? "Looking up…" : "Look up"}
      </button>
    </form>
  );
}

function ErrorBanner({ error }: { error: unknown }) {
  return (
    <div className="border border-rose-300 bg-rose-50 dark:bg-rose-950/30 dark:border-rose-700/40 rounded p-4 text-sm text-rose-800 dark:text-rose-200">
      {error instanceof Error ? error.message : "Lookup failed"}
    </div>
  );
}
