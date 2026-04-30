import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { EmptyState, Spinner, Page } from "../components/ui";
import type { FsPerson, FsPersonInput, FsBinding, FsBindingInput, Source } from "../types";
import type { PrincipalType } from "../lib/effectivePermsTypes";

const PRINCIPAL_TYPES: { value: PrincipalType; label: string }[] = [
  { value: "posix_uid",        label: "POSIX UID" },
  { value: "sid",              label: "Windows SID" },
  { value: "nfsv4_principal",  label: "NFSv4 principal" },
  { value: "s3_canonical",     label: "S3 canonical user" },
];

export default function SettingsIdentities() {
  const qc = useQueryClient();
  const personsQ = useQuery<FsPerson[]>({
    queryKey: ["identities"],
    queryFn:  () => api.get<FsPerson[]>("/identities"),
  });
  const sourcesQ = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn:  () => api.get<Source[]>("/sources"),
  });

  const createPerson = useMutation<FsPerson, Error, FsPersonInput>({
    mutationFn: (body) => api.post<FsPerson>("/identities", body),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });
  const deletePerson = useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`/identities/${id}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });

  return (
    <Page
      title="Identities"
      description="Tell Akashic who you are on each source. Search results filter by what these identities can read."
      width="compact"
    >
      {personsQ.isLoading ? (
        <div className="flex items-center justify-center py-12 text-fg-subtle">
          <Spinner />
        </div>
      ) : personsQ.isError ? (
        <div className="text-sm text-rose-600 bg-rose-50 rounded px-3 py-2 mb-4">
          {personsQ.error instanceof Error
            ? personsQ.error.message
            : "Failed to load identities"}
        </div>
      ) : (personsQ.data ?? []).length === 0 ? (
        <div className="border border-line rounded py-12 mb-4">
          <EmptyState
            title="No identities yet"
            description="Add one below to filter search by what you can read."
          />
        </div>
      ) : (
        <ul className="space-y-4">
          {(personsQ.data ?? []).map((p) => (
            <PersonCard
              key={p.id}
              person={p}
              sources={sourcesQ.data ?? []}
              onDelete={() => deletePerson.mutate(p.id)}
            />
          ))}
        </ul>
      )}

      <AddPersonForm onSubmit={(body) => createPerson.mutate(body)} pending={createPerson.isPending} />
    </Page>
  );
}

function PersonCard({
  person, sources, onDelete,
}: { person: FsPerson; sources: Source[]; onDelete: () => void }) {
  const qc = useQueryClient();

  const addBinding = useMutation<FsBinding, Error, FsBindingInput>({
    mutationFn: (body) => api.post<FsBinding>(`/identities/${person.id}/bindings`, body),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });
  const deleteBinding = useMutation<void, Error, string>({
    mutationFn: (bid) => api.delete<void>(`/identities/${person.id}/bindings/${bid}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });
  const resolveGroups = useMutation<FsBinding, Error, string>({
    mutationFn: (bid) => api.post<FsBinding>(`/identities/${person.id}/bindings/${bid}/resolve-groups`, {}),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["identities"] }),
  });

  return (
    <li className="border border-line rounded p-4 bg-surface">
      <div className="flex items-center justify-between mb-3">
        <div className="font-medium text-fg">
          {person.label}
          {person.is_primary && (
            <span className="ml-2 text-xs uppercase tracking-wider text-accent-700">primary</span>
          )}
        </div>
        <button
          type="button" onClick={onDelete}
          className="text-xs text-fg-subtle hover:text-red-600"
        >Delete identity</button>
      </div>

      {person.bindings.length === 0 && (
        <p className="text-xs text-fg-subtle italic mb-2">No bindings yet.</p>
      )}
      <ul className="space-y-1">
        {person.bindings.map((b) => {
          const source = sources.find((s) => s.id === b.source_id);
          return (
            <li key={b.id} className="flex items-center gap-3 text-sm">
              <span className="font-medium text-fg w-32 truncate">
                {source?.name ?? b.source_id.slice(0, 8)}
              </span>
              <code className="font-mono text-xs bg-surface-muted px-1.5 py-0.5 rounded">
                {b.identity_type}:{b.identifier}
              </code>
              {b.groups.length > 0 && (
                <span className="text-xs text-fg-muted">
                  groups: {b.groups.join(", ")}
                </span>
              )}
              {b.groups_resolved_at && b.groups_source === "auto" && (
                <span className="text-[10px] text-fg-subtle">
                  auto · {new Date(b.groups_resolved_at).toLocaleDateString()}
                </span>
              )}
              <button
                type="button"
                onClick={() => resolveGroups.mutate(b.id)}
                disabled={resolveGroups.isPending}
                className="text-xs text-accent-700 hover:text-accent-900 disabled:opacity-50"
                title="Auto-resolve groups from the source"
              >
                {resolveGroups.isPending ? "Resolving…" : "Resolve"}
              </button>
              <button
                type="button" onClick={() => deleteBinding.mutate(b.id)}
                className="ml-auto text-xs text-fg-subtle hover:text-red-600"
                aria-label="Remove binding"
              >×</button>
            </li>
          );
        })}
      </ul>

      <AddBindingForm
        sources={sources}
        existingSourceIds={new Set(person.bindings.map((b) => b.source_id))}
        onSubmit={(body) => addBinding.mutate(body)}
        pending={addBinding.isPending}
      />
      {resolveGroups.error && (
        <div className="text-xs text-rose-600 bg-rose-50 rounded px-2 py-1 mt-2">
          {resolveGroups.error instanceof Error ? resolveGroups.error.message : "Resolve failed"}
        </div>
      )}
    </li>
  );
}

function AddPersonForm({
  onSubmit, pending,
}: { onSubmit: (body: FsPersonInput) => void; pending: boolean }) {
  const [label, setLabel] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!label.trim()) return;
        onSubmit({ label: label.trim(), is_primary: isPrimary });
        setLabel(""); setIsPrimary(false);
      }}
      className="mt-6 flex items-center gap-2 text-sm"
    >
      <input
        type="text" value={label} onChange={(e) => setLabel(e.target.value)}
        placeholder="My Work Account"
        className="flex-1 border border-line rounded px-2 py-1"
      />
      <label className="text-xs text-fg-muted flex items-center gap-1">
        <input type="checkbox" checked={isPrimary} onChange={(e) => setIsPrimary(e.target.checked)} />
        Primary
      </label>
      <button
        type="submit" disabled={!label.trim() || pending}
        className="text-sm bg-accent-600 text-white rounded px-3 py-1 disabled:opacity-50 hover:bg-accent-700"
      >+ Add identity</button>
    </form>
  );
}

function AddBindingForm({
  sources, existingSourceIds, onSubmit, pending,
}: {
  sources: Source[];
  existingSourceIds: Set<string>;
  onSubmit: (body: FsBindingInput) => void;
  pending: boolean;
}) {
  const available = sources.filter((s) => !existingSourceIds.has(s.id));
  const [sourceId, setSourceId] = useState(available[0]?.id ?? "");
  const [type, setType] = useState<PrincipalType>("posix_uid");
  const [identifier, setIdentifier] = useState("");
  const [groupsRaw, setGroupsRaw] = useState("");

  if (available.length === 0) return null;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!sourceId || !identifier.trim()) return;
        onSubmit({
          source_id: sourceId,
          identity_type: type,
          identifier: identifier.trim(),
          groups: groupsRaw.split(",").map((g) => g.trim()).filter(Boolean),
        });
        setIdentifier(""); setGroupsRaw("");
      }}
      className="mt-3 flex items-center gap-2 text-xs"
    >
      <select
        value={sourceId} onChange={(e) => setSourceId(e.target.value)}
        className="border border-line rounded px-2 py-1"
      >
        {available.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
      <select
        value={type} onChange={(e) => setType(e.target.value as PrincipalType)}
        className="border border-line rounded px-2 py-1"
      >
        {PRINCIPAL_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
      </select>
      <input
        type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)}
        placeholder="identifier (e.g. 1000 or S-1-5-…)"
        className="flex-1 font-mono border border-line rounded px-2 py-1"
      />
      <input
        type="text" value={groupsRaw} onChange={(e) => setGroupsRaw(e.target.value)}
        placeholder="groups (comma-sep)"
        className="w-48 font-mono border border-line rounded px-2 py-1"
      />
      <button
        type="submit" disabled={!identifier.trim() || pending}
        className="bg-accent-600 text-white rounded px-2 py-1 disabled:opacity-50 hover:bg-accent-700"
      >+ Add binding</button>
    </form>
  );
}
