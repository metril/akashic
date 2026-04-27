import type { S3ACL as S3ACLType } from "../../types";
import { Mono } from "./shared";

export function S3ACL({ acl }: { acl: S3ACLType }) {
  return (
    <div>
      {acl.owner && (
        <p className="text-sm text-gray-700 mb-3">
          Owner: <span className="font-medium">{acl.owner.display_name || acl.owner.id}</span>{" "}
          <span className="text-xs text-gray-400 ml-1.5">({acl.owner.id})</span>
        </p>
      )}
      {acl.grants.length === 0 ? (
        <p className="text-sm text-gray-400 italic">
          No object ACL grants — bucket-owner enforced. See source for bucket policy.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] text-gray-400 uppercase tracking-wide">
              <th className="text-left py-1 font-semibold">Type</th>
              <th className="text-left py-1 font-semibold">Grantee</th>
              <th className="text-left py-1 font-semibold">Permission</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {acl.grants.map((g, i) => (
              <tr key={i}>
                <td className="py-1.5"><Mono>{g.grantee_type}</Mono></td>
                <td className="py-1.5 text-gray-700">{g.grantee_name || g.grantee_id || "—"}</td>
                <td className="py-1.5"><Mono>{g.permission}</Mono></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
