import { useMemo, useState } from "react";
import type { NtACL as NtACLType, NtACE, NtPrincipal } from "../../types";
import { Chip, Mono } from "./shared";
import { formatNtMask, formatAceFlag, formatNtControl } from "../../lib/aclLabels";
import { useResolvePrincipals } from "../../hooks/useResolvePrincipals";
import type { PrincipalMap, ResolvedPrincipal } from "../../hooks/useResolvePrincipals";

// `name` is empty when the scanner couldn't translate the SID at scan
// time — typically because LSARPC was unreachable from the scanner
// (DC down, network ACLs blocking it). The on-demand resolver hook
// re-asks LSARPC at view-time and merges any names it gets back; what
// reaches this component is whatever both passes yielded.
//
// Tooltip text adapts to status so the user can tell apart "we tried
// and the DC was down" from "we never asked because the source isn't
// SMB" from "DC said it doesn't know this SID".
function UnresolvedSid({ sid, resolved }: { sid: string; resolved?: ResolvedPrincipal }) {
  let title: string;
  if (!resolved || resolved.status === "error") {
    title =
      "Couldn't resolve this SID — either the scanner can't reach the " +
      "domain controller right now, or the lookup itself failed. " +
      "Re-open the entry to retry.";
  } else if (resolved.status === "skipped") {
    title =
      "SID resolution isn't available for this source type (the source " +
      "is not an SMB share with an LSARPC endpoint).";
  } else {
    // status === "unresolved" — DC was reached and said it didn't know
    title =
      "The domain controller didn't recognize this SID. The principal " +
      "may have been deleted, or the SID may belong to a foreign " +
      "domain that the queried server can't translate.";
  }
  return (
    <span className="italic text-gray-500" title={title}>
      <Mono>{sid}</Mono>
    </span>
  );
}

// Resolved name for display: prefer the scanner-side translation
// already in the ACL JSON; fall back to the on-demand resolver map;
// return null if neither has anything (caller draws UnresolvedSid).
function displayName(
  baseName: string | undefined,
  sid: string,
  map: PrincipalMap,
): string | null {
  if (baseName) return baseName;
  const r = map[sid];
  if (r && r.name) return r.name;
  return null;
}

function PrincipalRow({
  label, p, map,
}: {
  label: string;
  p: NtPrincipal | null;
  map: PrincipalMap;
}) {
  if (!p) return null;
  const name = displayName(p.name, p.sid, map);
  return (
    <div className="flex items-baseline gap-3 text-sm py-1">
      <dt className="w-20 flex-shrink-0 text-xs text-gray-500">{label}</dt>
      <dd className="min-w-0 flex-1 text-gray-800 break-words">
        {name ? (
          <>
            <span className="font-medium">{name}</span>
            <span className="text-xs text-gray-400 ml-2"><Mono>{p.sid}</Mono></span>
          </>
        ) : (
          <UnresolvedSid sid={p.sid} resolved={map[p.sid]} />
        )}
      </dd>
    </div>
  );
}

function ACERow({
  ace, index, map,
}: {
  ace: NtACE;
  index: number;
  map: PrincipalMap;
}) {
  const name = displayName(ace.name, ace.sid, map);
  return (
    <tr>
      <td className="py-1.5 text-gray-400 tabular-nums">{index + 1}</td>
      <td className="py-1.5 text-gray-800">
        {name ? name : <UnresolvedSid sid={ace.sid} resolved={map[ace.sid]} />}
      </td>
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

// Collect every SID in the ACL whose `name` is missing — those are
// the only SIDs worth a roundtrip. Built-in SIDs the scanner already
// named at scan time (BUILTIN\Administrators, SYSTEM, etc.) are
// skipped, so we don't waste a network call on them.
function unresolvedSidsFromAcl(acl: NtACLType): string[] {
  const out = new Set<string>();
  if (acl.owner && !acl.owner.name) out.add(acl.owner.sid);
  if (acl.group && !acl.group.name) out.add(acl.group.sid);
  for (const ace of acl.entries) {
    if (!ace.name) out.add(ace.sid);
  }
  return [...out];
}

export function NtACL({ acl, sourceId }: { acl: NtACLType; sourceId?: string }) {
  const [showInherited, setShowInherited] = useState(false);
  const inherited = acl.entries.filter(a => a.flags.includes("inherited"));
  const direct = acl.entries.filter(a => !a.flags.includes("inherited"));

  // Resolve any SIDs the scanner couldn't translate. The hook is a
  // no-op (returns EMPTY) when there are zero unresolved SIDs or no
  // sourceId — both mean "nothing to ask the api about".
  const unresolvedSids = useMemo(() => unresolvedSidsFromAcl(acl), [acl]);
  const { data: principalMap } = useResolvePrincipals(sourceId, unresolvedSids);

  return (
    <div>
      <dl className="mb-3">
        <PrincipalRow label="Owner" p={acl.owner} map={principalMap} />
        <PrincipalRow label="Group" p={acl.group} map={principalMap} />
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
          {direct.map((a, i) => <ACERow key={i} ace={a} index={i} map={principalMap} />)}
          {showInherited && inherited.map((a, i) => (
            <ACERow key={`i${i}`} ace={a} index={direct.length + i} map={principalMap} />
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
