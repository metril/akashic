import { useState } from "react";
import type { NtACL as NtACLType, NtACE, NtPrincipal } from "../../types";
import { Chip, Mono } from "./shared";
import { formatNtMask, formatAceFlag, formatNtControl } from "../../lib/aclLabels";

function PrincipalRow({ label, p }: { label: string; p: NtPrincipal | null }) {
  if (!p) return null;
  return (
    <div className="flex items-baseline gap-3 text-sm py-1">
      <dt className="w-20 flex-shrink-0 text-xs text-gray-500">{label}</dt>
      <dd className="min-w-0 flex-1 text-gray-800 break-words">
        <span className="font-medium">{p.name || p.sid}</span>
        {p.name && <span className="text-xs text-gray-400 ml-2"><Mono>{p.sid}</Mono></span>}
      </dd>
    </div>
  );
}

function ACERow({ ace, index }: { ace: NtACE; index: number }) {
  return (
    <tr>
      <td className="py-1.5 text-gray-400 tabular-nums">{index + 1}</td>
      <td className="py-1.5 text-gray-800">{ace.name || ace.sid}</td>
      <td className="py-1.5">
        <Chip variant={ace.ace_type === "deny" ? "deny" : "allow"}>{ace.ace_type}</Chip>
      </td>
      <td className="py-1.5">
        <div className="flex flex-wrap gap-1">
          {ace.flags.map((f) => (<Chip key={f} variant="muted">{formatAceFlag(f)}</Chip>))}
          {ace.flags.length === 0 && <span className="text-gray-400">—</span>}
        </div>
      </td>
      <td className="py-1.5">
        <div className="flex flex-wrap gap-1">
          {ace.mask.map((m) => (<Chip key={m} variant="neutral">{formatNtMask(m)}</Chip>))}
        </div>
      </td>
    </tr>
  );
}

export function NtACL({ acl }: { acl: NtACLType }) {
  const [showInherited, setShowInherited] = useState(false);
  const inherited = acl.entries.filter(a => a.flags.includes("inherited"));
  const direct = acl.entries.filter(a => !a.flags.includes("inherited"));

  return (
    <div>
      <dl className="mb-3">
        <PrincipalRow label="Owner" p={acl.owner} />
        <PrincipalRow label="Group" p={acl.group} />
      </dl>
      {acl.control.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {acl.control.map((c) => (
            <Chip key={c} variant="muted">{formatNtControl(c)}</Chip>
          ))}
        </div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] text-gray-400 uppercase tracking-wide">
            <th className="text-left py-1 font-semibold">#</th>
            <th className="text-left py-1 font-semibold">Principal</th>
            <th className="text-left py-1 font-semibold">Type</th>
            <th className="text-left py-1 font-semibold">Flags</th>
            <th className="text-left py-1 font-semibold">Permissions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {direct.map((a, i) => <ACERow key={i} ace={a} index={i} />)}
          {showInherited && inherited.map((a, i) => (
            <ACERow key={`i${i}`} ace={a} index={direct.length + i} />
          ))}
        </tbody>
      </table>
      {inherited.length > 0 && (
        <button
          type="button"
          onClick={() => setShowInherited(!showInherited)}
          className="mt-2 text-xs text-accent-600 hover:underline"
        >
          {showInherited ? "Hide" : "Show"} {inherited.length} inherited entr{inherited.length === 1 ? "y" : "ies"}
        </button>
      )}
    </div>
  );
}
