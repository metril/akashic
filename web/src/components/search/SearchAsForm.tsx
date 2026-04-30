import { useState } from "react";
import type { SearchAsOverride } from "../../types";
import type { PrincipalType } from "../../lib/effectivePermsTypes";

const PRINCIPAL_TYPES: { value: PrincipalType; label: string }[] = [
  { value: "posix_uid",        label: "POSIX UID" },
  { value: "sid",              label: "Windows SID" },
  { value: "nfsv4_principal",  label: "NFSv4 principal" },
  { value: "s3_canonical",     label: "S3 canonical user" },
];

export function SearchAsForm({
  value, onChange,
}: {
  value: SearchAsOverride | null;
  onChange: (v: SearchAsOverride | null) => void;
}) {
  const [type, setType]             = useState<PrincipalType>(value?.type ?? "posix_uid");
  const [identifier, setIdentifier] = useState(value?.identifier ?? "");
  const [groupsRaw, setGroupsRaw]   = useState((value?.groups ?? []).join(", "));

  function apply() {
    if (!identifier.trim()) {
      onChange(null);
      return;
    }
    onChange({
      type, identifier: identifier.trim(),
      groups: groupsRaw.split(",").map((g) => g.trim()).filter(Boolean),
    });
  }

  function clear() {
    setIdentifier(""); setGroupsRaw("");
    onChange(null);
  }

  return (
    <div className="border border-amber-200 bg-amber-50 rounded p-3 mb-3">
      <div className="text-xs font-medium text-amber-900 mb-2">
        Search as another principal (audit-logged)
      </div>
      <div className="flex flex-wrap items-end gap-2 text-xs">
        <select
          value={type} onChange={(e) => setType(e.target.value as PrincipalType)}
          className="border border-amber-200 rounded px-2 py-1 bg-surface"
        >
          {PRINCIPAL_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <input
          type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)}
          placeholder="identifier (e.g. 1000 or S-1-5-…)"
          className="flex-1 min-w-[160px] font-mono border border-amber-200 rounded px-2 py-1 bg-surface"
        />
        <input
          type="text" value={groupsRaw} onChange={(e) => setGroupsRaw(e.target.value)}
          placeholder="groups (comma-sep)"
          className="w-48 font-mono border border-amber-200 rounded px-2 py-1 bg-surface"
        />
        <button
          type="button" onClick={apply}
          disabled={!identifier.trim()}
          className="bg-amber-600 text-white rounded px-3 py-1 disabled:opacity-50 hover:bg-amber-700"
        >Apply</button>
        {value !== null && (
          <button
            type="button" onClick={clear}
            className="text-amber-700 hover:text-amber-900 px-2 py-1"
          >Clear</button>
        )}
      </div>
    </div>
  );
}
