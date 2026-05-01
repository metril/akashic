import { useState } from "react";

import { Button, Input, Select } from "../ui";

const PRINCIPAL_KIND_OPTIONS = [
  { value: "sid",        label: "AD SID (S-1-5-…)" },
  { value: "posix:uid",  label: "POSIX UID" },
  { value: "posix:gid",  label: "POSIX GID" },
  { value: "nfsv4",      label: "NFSv4 user" },
  { value: "nfsv4:GROUP", label: "NFSv4 group" },
  { value: "*",          label: "Anyone (wildcard)" },
  { value: "auth",       label: "Authenticated users" },
];

interface Props {
  onLookup: (token: string) => void;
  pending?: boolean;
}

/** Two-field input: a kind dropdown ("AD SID", "POSIX UID", …) plus a
 * value field. The page composes the canonical token (`sid:S-…`,
 * `posix:uid:1001`, …) on submit, sparing admins from remembering the
 * token vocabulary.
 *
 * Wildcard kinds (`*` and `auth`) hide the value field — they have no
 * argument to take. */
export function PrincipalLookup({ onLookup, pending }: Props) {
  const [kind, setKind] = useState("sid");
  const [value, setValue] = useState("");

  const isWildcard = kind === "*" || kind === "auth";
  const canSubmit = isWildcard || value.trim().length > 0;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    if (isWildcard) {
      onLookup(kind);
    } else {
      onLookup(`${kind}:${value.trim()}`);
    }
  };

  return (
    <form onSubmit={submit} className="flex items-end gap-3">
      <div className="w-56">
        <Select
          label="Kind"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          options={PRINCIPAL_KIND_OPTIONS}
        />
      </div>
      {!isWildcard && (
        <div className="flex-1">
          <Input
            label="Value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={kind === "sid" ? "S-1-5-21-…-1001" : "1001"}
            className="font-mono"
          />
        </div>
      )}
      <Button type="submit" disabled={!canSubmit || pending}>
        {pending ? "Looking up…" : "Look up"}
      </Button>
    </form>
  );
}
