import type { S3ACL as S3ACLType } from "../../types";

export function S3ACL({ acl: _acl }: { acl: S3ACLType }) {
  return <p className="text-sm text-gray-500 italic">S3 renderer arrives in Phase 7.</p>;
}
