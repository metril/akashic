import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../../api/client";
import type {
  ACL,
  EffectivePerms,
  EffectivePermsRequest,
  GroupRef,
  PrincipalType,
  RightName,
} from "../../types";
import { Section, Chip } from "./shared";

const RIGHT_LABELS: Record<RightName, string> = {
  read: "Read",
  write: "Write",
  execute: "Execute",
  delete: "Delete",
  change_perms: "Change permissions",
};

const PRINCIPAL_TYPES: { value: PrincipalType; label: string }[] = [
  { value: "posix_uid",        label: "POSIX UID" },
  { value: "sid",              label: "SID (Windows)" },
  { value: "nfsv4_principal",  label: "NFSv4 principal" },
  { value: "s3_canonical",     label: "S3 canonical user" },
];

function defaultPrincipalType(acl: ACL | null): PrincipalType {
  if (!acl) return "posix_uid";
  switch (acl.type) {
    case "posix":  return "posix_uid";
    case "nfsv4":  return "nfsv4_principal";
    case "nt":     return "sid";
    case "s3":     return "s3_canonical";
  }
}

export function EffectivePermissions({
  entryId,
  acl,
}: {
  entryId: string;
  acl: ACL | null;
}) {
  const [principalType, setPrincipalType] = useState<PrincipalType>(defaultPrincipalType(acl));
  const [identifier, setIdentifier] = useState("");
  const [groups, setGroups] = useState<GroupRef[]>([]);

  const mutation = useMutation<EffectivePerms, Error, EffectivePermsRequest>({
    mutationFn: (body) =>
      api.post<EffectivePerms>(`/entries/${entryId}/effective-permissions`, body),
  });

  const submit = () => {
    if (!identifier.trim()) return;
    mutation.mutate({
      principal: { type: principalType, identifier: identifier.trim() },
      groups: groups.filter((g) => g.identifier.trim() !== ""),
    });
  };

  return (
    <Section title="Effective permissions">
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-fg-muted flex flex-col">
            Principal type
            <select
              className="mt-1 text-sm border border-line rounded px-2 py-1"
              value={principalType}
              onChange={(e) => setPrincipalType(e.target.value as PrincipalType)}
            >
              {PRINCIPAL_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </label>
          <label className="text-xs text-fg-muted flex flex-col flex-1 min-w-[160px]">
            Identifier
            <input
              type="text"
              className="mt-1 text-sm font-mono border border-line rounded px-2 py-1"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder={principalType === "sid" ? "S-1-5-21-..." : "1000"}
            />
          </label>
        </div>

        {groups.map((g, i) => (
          <div key={i} className="flex items-center gap-2">
            <select
              className="text-xs border border-line rounded px-2 py-1"
              value={g.type}
              onChange={(e) => {
                const next = [...groups];
                next[i] = { ...g, type: e.target.value as PrincipalType };
                setGroups(next);
              }}
            >
              {PRINCIPAL_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <input
              type="text"
              className="flex-1 text-sm font-mono border border-line rounded px-2 py-1"
              placeholder="group identifier"
              value={g.identifier}
              onChange={(e) => {
                const next = [...groups];
                next[i] = { ...g, identifier: e.target.value };
                setGroups(next);
              }}
            />
            <button
              type="button"
              onClick={() => setGroups(groups.filter((_, j) => j !== i))}
              className="text-xs text-fg-subtle hover:text-red-600 px-2"
              aria-label="Remove group"
            >×</button>
          </div>
        ))}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setGroups([...groups, { type: principalType, identifier: "" }])}
            className="text-xs text-accent-600 hover:text-accent-800"
          >+ Add group</button>
          <button
            type="button"
            onClick={submit}
            disabled={!identifier.trim() || mutation.isPending}
            className="text-sm bg-accent-600 text-white rounded px-3 py-1 disabled:opacity-50 hover:bg-accent-700"
          >
            {mutation.isPending ? "Computing…" : "Compute"}
          </button>
        </div>

        {mutation.error && (
          <div className="text-sm text-red-700 bg-red-50 rounded px-3 py-2">
            {mutation.error.message}
          </div>
        )}

        {mutation.data && (
          <div className="mt-2 border border-line rounded">
            {mutation.data.evaluated_with.caveats.length > 0 && (
              <div className="px-3 py-2 border-b border-line bg-amber-50 text-xs text-amber-800 space-y-1">
                {mutation.data.evaluated_with.caveats.map((c, i) => (
                  <div key={i}>⚠ {c}</div>
                ))}
              </div>
            )}
            <table className="w-full text-sm">
              <tbody>
                {(["read","write","execute","delete","change_perms"] as RightName[]).map((r) => {
                  const result = mutation.data!.rights[r];
                  return (
                    <tr key={r} className="border-t border-line-subtle first:border-t-0">
                      <td className="px-3 py-1.5 text-fg w-1/4">{RIGHT_LABELS[r]}</td>
                      <td className="px-3 py-1.5 w-12 text-center">
                        {result.granted
                          ? <Chip variant="allow">✓</Chip>
                          : <Chip variant="deny">✗</Chip>}
                      </td>
                      <td className="px-3 py-1.5 text-xs text-fg-muted font-mono break-all">
                        {result.by.length > 0
                          ? result.by.map((b) => b.summary).join("; ")
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Section>
  );
}
