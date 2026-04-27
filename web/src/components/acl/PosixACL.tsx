import type { PosixACL as PosixACLType, PosixACE } from "../../types";
import { Mono, Subheader } from "./shared";

function ACETable({ entries }: { entries: PosixACE[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-[11px] text-gray-400 uppercase tracking-wide">
          <th className="text-left py-1 font-semibold">Tag</th>
          <th className="text-left py-1 font-semibold">Qualifier</th>
          <th className="text-left py-1 font-semibold">Perms</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-100">
        {entries.map((a, i) => (
          <tr key={i}>
            <td className="py-1.5"><Mono>{a.tag}</Mono></td>
            <td className="py-1.5 text-gray-700">{a.qualifier || "—"}</td>
            <td className="py-1.5"><Mono>{a.perms}</Mono></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PosixACL({ acl }: { acl: PosixACLType }) {
  return (
    <div>
      <ACETable entries={acl.entries} />
      {acl.default_entries && acl.default_entries.length > 0 && (
        <>
          <Subheader>Default ACL (inherited by children)</Subheader>
          <ACETable entries={acl.default_entries} />
        </>
      )}
    </div>
  );
}
