import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, ok } from "../api/client";
import { Button, Card, Drawer, EmptyState, Page, Spinner } from "../components/ui";
import { EntryDetail } from "../components/EntryDetail";
import { IdentityPicker } from "../components/access/IdentityPicker";
import { DirectorySyncBar } from "../components/access/DirectorySyncBar";
import { PrincipalLookup } from "../components/access/PrincipalLookup";
import {
  FileToPrincipalsResults,
  PrincipalToFilesResults,
} from "../components/access/AccessResults";
import type { components } from "../api/schema";

type S = components["schemas"];


type Mode = "principal" | "file";

type PrincipalResult = S["PrincipalAccessResponse"];

type FileResult = S["FileAccessResponse"];

/** Admin "blast radius" page — answers two complementary questions:
 * "what files can principal X reach?" and "who can reach file Y?".
 *
 * Both questions share the same /api/v1/access endpoint (one query param
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
  const [identityId, setIdentityId] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [fileId, setFileId] = useState<string | null>(null);
  const [openEntryId, setOpenEntryId] = useState<string | null>(null);

  const principalQ = useQuery<PrincipalResult>({
    queryKey: ["access", "principal", token, right],
    queryFn: async () => {
      const r = ok(
        await client.GET("/api/v1/access", {
          params: { query: { principal: token!, right, limit: 20 } },
        }),
      );
      if (!("principal" in r)) throw new Error("Unexpected access response");
      return r;
    },
    enabled: mode === "principal" && Boolean(token),
  });

  const qc = useQueryClient();
  const identityQ = useQuery<PrincipalResult>({
    queryKey: ["access", "identity", identityId, right],
    queryFn: async () => {
      const r = ok(
        await client.GET("/api/v1/access", {
          params: { query: { identity: identityId!, right, limit: 20 } },
        }),
      );
      if (!("principal" in r)) throw new Error("Unexpected access response");
      return r;
    },
    enabled: mode === "principal" && !advanced && Boolean(identityId),
  });

  // The response's identity id is canonical (it can differ from the one asked for).
  const refreshId = identityQ.data?.identity?.id ?? identityId;
  const refresh = useMutation({
    mutationFn: async () =>
      ok(await client.POST("/api/v1/access/identities/refresh", { body: { id: refreshId! } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["access", "identity", identityId] }),
  });

  const fileQ = useQuery<FileResult>({
    queryKey: ["access", "file", fileId, right],
    queryFn: async () => {
      const r = ok(
        await client.GET("/api/v1/access", { params: { query: { file: fileId!, right } } }),
      );
      if (!("entry_id" in r)) throw new Error("Unexpected access response");
      return r;
    },
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
        <Card padding="md" className="mb-5 space-y-4">
          <DirectorySyncBar />
          {advanced ? (
            <PrincipalLookup
              onLookup={setToken}
              pending={principalQ.isFetching && Boolean(token)}
            />
          ) : (
            <IdentityPicker onPick={setIdentityId} />
          )}
          <button
            type="button"
            aria-pressed={advanced}
            onClick={() => setAdvanced((a) => !a)}
            className="text-xs text-fg-muted hover:text-fg underline"
          >
            Advanced: raw token
          </button>
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

      {mode === "principal" && !advanced ? (
        identityId == null ? (
          <EmptyState
            title="Look up a person or group"
            description="Search above and pick a result to see every file they can reach."
          />
        ) : identityQ.isLoading ? (
          <div className="flex justify-center py-12 text-fg-subtle"><Spinner /></div>
        ) : identityQ.error ? (
          <ErrorBanner error={identityQ.error} />
        ) : identityQ.data ? (
          <PrincipalToFilesResults
            data={identityQ.data}
            onSelectEntry={setOpenEntryId}
            onRefresh={() => refresh.mutate()}
            refreshing={refresh.isPending}
            refreshError={refresh.error ? refresh.error.message : null}
          />
        ) : null
      ) : mode === "principal" ? (
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
      <Button
        type="submit"
        disabled={!input.trim()}
        loading={pending}
      >
        Look up
      </Button>
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
