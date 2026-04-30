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

interface ACLSectionProps {
  acl: ACL | null;
  // sourceId is required for NT ACLs — needed for the on-demand SID
  // resolver to know which domain controller to ask via the scanner.
  // Other ACL flavors don't need it (POSIX/NFSv4 carry uid/gid
  // directly; S3 has names already), so it's optional. EntryDetail
  // always passes it; older callers that only render POSIX can omit.
  sourceId?: string;
}

export function ACLSection({ acl, sourceId }: ACLSectionProps) {
  if (!acl) {
    return <Section title="ACL" empty>None</Section>;
  }
  const title = TITLE[acl.type];
  switch (acl.type) {
    case "posix": return <Section title={title}><PosixACL acl={acl} /></Section>;
    case "nfsv4": return <Section title={title}><NfsV4ACL acl={acl} /></Section>;
    case "nt":    return <Section title={title}><NtACL    acl={acl} sourceId={sourceId} /></Section>;
    case "s3":    return <Section title={title}><S3ACL    acl={acl} /></Section>;
  }
}
