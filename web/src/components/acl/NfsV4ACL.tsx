import type { NfsV4ACL as NfsV4ACLType } from "../../types";

export function NfsV4ACL({ acl: _acl }: { acl: NfsV4ACLType }) {
  return <p className="text-sm text-gray-500 italic">NFSv4 renderer arrives in Phase 4.</p>;
}
