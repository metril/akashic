import type { ACL, ACLType } from "../../types";
import { Section } from "./shared";
import { PosixACL } from "./PosixACL";
import { NfsV4ACL } from "./NfsV4ACL";
import { NtACL } from "./NtACL";
import { S3ACL } from "./S3ACL";

const TITLE: Record<ACLType, string> = {
  posix: "POSIX ACL",
  nfsv4: "NFSv4 ACL",
  nt:    "NT ACL",
  s3:    "S3 ACL",
};

export function ACLSection({ acl }: { acl: ACL | null }) {
  if (!acl) {
    return <Section title="ACL" empty>None</Section>;
  }
  const title = TITLE[acl.type];
  switch (acl.type) {
    case "posix": return <Section title={title}><PosixACL acl={acl} /></Section>;
    case "nfsv4": return <Section title={title}><NfsV4ACL acl={acl} /></Section>;
    case "nt":    return <Section title={title}><NtACL    acl={acl} /></Section>;
    case "s3":    return <Section title={title}><S3ACL    acl={acl} /></Section>;
  }
}
