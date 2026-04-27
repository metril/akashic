import type { NfsV4ACL as NfsV4ACLType, NfsV4ACE } from "../../types";
import { Chip } from "./shared";
import { formatNfsV4Mask, formatAceFlag } from "../../lib/aclLabels";

function ACERow({ ace, index }: { ace: NfsV4ACE; index: number }) {
  return (
    <tr>
      <td className="py-1.5 text-gray-400 tabular-nums">{index + 1}</td>
      <td className="py-1.5 text-gray-800">{ace.principal}</td>
      <td className="py-1.5">
        <Chip variant={ace.ace_type === "deny" ? "deny" : "allow"}>
          {ace.ace_type}
        </Chip>
      </td>
      <td className="py-1.5">
        <div className="flex flex-wrap gap-1">
          {ace.flags.map((f) => (
            <Chip key={f} variant="muted">{formatAceFlag(f)}</Chip>
          ))}
          {ace.flags.length === 0 && <span className="text-gray-400">—</span>}
        </div>
      </td>
      <td className="py-1.5">
        <div className="flex flex-wrap gap-1">
          {ace.mask.map((m) => (
            <Chip key={m} variant="neutral">{formatNfsV4Mask(m)}</Chip>
          ))}
        </div>
      </td>
    </tr>
  );
}

export function NfsV4ACL({ acl }: { acl: NfsV4ACLType }) {
  return (
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
        {acl.entries.map((a, i) => (
          <ACERow key={i} ace={a} index={i} />
        ))}
      </tbody>
    </table>
  );
}
