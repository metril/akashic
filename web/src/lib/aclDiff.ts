import type { ACL, ACLType, PosixACE, PosixACL, NfsV4ACE, NfsV4ACL, NtACE, NtACL, NtPrincipal, S3ACL, S3Grant, S3Owner } from "../types";

export type ACLDiffKind =
  | "type_changed"
  | "added"
  | "removed"
  | "modified"
  | "reordered"
  | "owner_changed"
  | "group_changed";

export type ACLDiffItem =
  | { kind: "type_changed"; from: ACLType | "none"; to: ACLType | "none" }
  | { kind: "added";        scope?: string; summary: string }
  | { kind: "removed";      scope?: string; summary: string }
  | { kind: "modified";     scope?: string; summary: string }
  | { kind: "reordered";    scope?: string; summary: string }
  | { kind: "owner_changed"; from: string; to: string }
  | { kind: "group_changed"; from: string; to: string };

export function diffACL(prev: ACL | null, curr: ACL | null): ACLDiffItem[] {
  if (prev === null && curr === null) return [];
  if (prev === null && curr !== null) return [{ kind: "type_changed", from: "none", to: curr.type }];
  if (prev !== null && curr === null) return [{ kind: "type_changed", from: prev.type, to: "none" }];
  if (prev!.type !== curr!.type) {
    return [{ kind: "type_changed", from: prev!.type, to: curr!.type }];
  }
  if (prev!.type === "nfsv4" && curr!.type === "nfsv4") return diffNfsV4(prev as NfsV4ACL, curr as NfsV4ACL);
  if (prev!.type === "posix" && curr!.type === "posix") return diffPosix(prev as PosixACL, curr as PosixACL);
  if (prev!.type === "nt" && curr!.type === "nt") return diffNt(prev as NtACL, curr as NtACL);
  if (prev!.type === "s3" && curr!.type === "s3") return diffS3(prev as S3ACL, curr as S3ACL);
  return [];
}

function posixKey(ace: PosixACE): string {
  return ace.qualifier ? `${ace.tag}:${ace.qualifier}` : ace.tag;
}

function posixSummary(ace: PosixACE): string {
  return `${posixKey(ace)} ${ace.perms}`;
}

function diffPosixEntries(
  prev: PosixACE[],
  curr: PosixACE[],
  scope?: string,
): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];
  const prevByKey = new Map(prev.map((a) => [posixKey(a), a]));
  const currByKey = new Map(curr.map((a) => [posixKey(a), a]));

  // Stable order: walk the union of keys in insertion order of prev then curr.
  const seen = new Set<string>();
  const orderedKeys = [
    ...prev.map(posixKey),
    ...curr.map(posixKey).filter((k) => !prevByKey.has(k)),
  ].filter((k) => (seen.has(k) ? false : (seen.add(k), true)));

  for (const k of orderedKeys) {
    const a = prevByKey.get(k);
    const b = currByKey.get(k);
    if (a && !b) out.push({ kind: "removed", ...(scope ? { scope } : {}), summary: posixSummary(a) });
    else if (!a && b) out.push({ kind: "added", ...(scope ? { scope } : {}), summary: posixSummary(b) });
    else if (a && b && a.perms !== b.perms) {
      out.push({
        kind: "modified",
        ...(scope ? { scope } : {}),
        summary: `${posixKey(a)} ${a.perms} → ${b.perms}`,
      });
    }
  }
  return out;
}

function diffPosix(prev: PosixACL, curr: PosixACL): ACLDiffItem[] {
  const access = diffPosixEntries(prev.entries, curr.entries);
  const prevDefault = prev.default_entries ?? [];
  const currDefault = curr.default_entries ?? [];
  const def = diffPosixEntries(prevDefault, currDefault, "default");
  return [...access, ...def];
}

// ── NFSv4 ────────────────────────────────────────────────────────────────────

function nfsKey(ace: NfsV4ACE): string {
  // (principal, ace_type) — semantic identity for "is this the same ACE".
  return `${ace.principal}\x00${ace.ace_type}`;
}

function nfsSummary(ace: NfsV4ACE): string {
  const flags = ace.flags.length ? ` [${ace.flags.join(",")}]` : "";
  return `${ace.principal} ${ace.ace_type} ${ace.mask.join(",")}${flags}`;
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function nfsAcesEqual(a: NfsV4ACE, b: NfsV4ACE): boolean {
  return (
    a.principal === b.principal &&
    a.ace_type === b.ace_type &&
    arraysEqual(a.mask, b.mask) &&
    arraysEqual(a.flags, b.flags)
  );
}

function diffOrderedAces<T>(
  prev: T[],
  curr: T[],
  keyFn: (t: T) => string,
  eqFn: (a: T, b: T) => boolean,
  summaryFn: (t: T) => string,
  modifiedSummary: (a: T, b: T) => string,
  scope?: string,
): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];

  const prevByKey = new Map(prev.map((a) => [keyFn(a), a]));
  const currByKey = new Map(curr.map((a) => [keyFn(a), a]));

  // Removed (in prev, not in curr).
  for (const a of prev) {
    if (!currByKey.has(keyFn(a))) {
      out.push({ kind: "removed", ...(scope ? { scope } : {}), summary: summaryFn(a) });
    }
  }
  // Added or modified (in curr).
  for (const b of curr) {
    const a = prevByKey.get(keyFn(b));
    if (!a) {
      out.push({ kind: "added", ...(scope ? { scope } : {}), summary: summaryFn(b) });
    } else if (!eqFn(a, b)) {
      out.push({ kind: "modified", ...(scope ? { scope } : {}), summary: modifiedSummary(a, b) });
    }
  }

  // Reorder detection: relative order of kept keys.
  const keptPrev = prev.map(keyFn).filter((k) => currByKey.has(k));
  const keptCurr = curr.map(keyFn).filter((k) => prevByKey.has(k));
  if (!arraysEqual(keptPrev, keptCurr)) {
    out.push({
      kind: "reordered",
      ...(scope ? { scope } : {}),
      summary: "ACE order changed (significant for evaluation)",
    });
  }
  return out;
}

function diffNfsV4(prev: NfsV4ACL, curr: NfsV4ACL): ACLDiffItem[] {
  return diffOrderedAces(
    prev.entries,
    curr.entries,
    nfsKey,
    nfsAcesEqual,
    nfsSummary,
    (a, b) => `${a.principal} ${a.ace_type} ${a.mask.join(",")} → ${b.mask.join(",")}`,
  );
}

// ── NT ───────────────────────────────────────────────────────────────────────

function ntKey(ace: NtACE): string {
  return `${ace.sid}\x00${ace.ace_type}`;
}

function ntSummary(ace: NtACE): string {
  const label = ace.name || ace.sid;
  const flags = ace.flags.filter((f) => f !== "inherited");
  const flagStr = flags.length ? ` [${flags.join(",")}]` : "";
  const inherited = ace.flags.includes("inherited") ? " [inherited]" : "";
  return `${label} ${ace.ace_type} ${ace.mask.join(",")}${flagStr}${inherited}`;
}

function ntAcesEqual(a: NtACE, b: NtACE): boolean {
  return (
    a.sid === b.sid &&
    a.ace_type === b.ace_type &&
    arraysEqual(a.mask, b.mask) &&
    arraysEqual(a.flags, b.flags)
  );
}

function principalLabel(p: NtPrincipal | null): string {
  if (!p) return "(none)";
  return p.name || p.sid;
}

function isInherited(ace: NtACE): boolean {
  return ace.flags.includes("inherited");
}

function diffNt(prev: NtACL, curr: NtACL): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];

  // Owner / group first.
  const prevOwner = principalLabel(prev.owner);
  const currOwner = principalLabel(curr.owner);
  if (prevOwner !== currOwner) {
    out.push({ kind: "owner_changed", from: prevOwner, to: currOwner });
  }
  const prevGroup = principalLabel(prev.group);
  const currGroup = principalLabel(curr.group);
  if (prevGroup !== currGroup) {
    out.push({ kind: "group_changed", from: prevGroup, to: currGroup });
  }

  // Direct ACEs.
  const prevDirect = prev.entries.filter((a) => !isInherited(a));
  const currDirect = curr.entries.filter((a) => !isInherited(a));
  out.push(
    ...diffOrderedAces(
      prevDirect,
      currDirect,
      ntKey,
      ntAcesEqual,
      ntSummary,
      (a, b) => `${a.name || a.sid} ${a.ace_type} ${a.mask.join(",")} → ${b.mask.join(",")}`,
    ),
  );

  // Inherited ACEs (separate scope so the renderer can collapse).
  const prevInherited = prev.entries.filter(isInherited);
  const currInherited = curr.entries.filter(isInherited);
  out.push(
    ...diffOrderedAces(
      prevInherited,
      currInherited,
      ntKey,
      ntAcesEqual,
      ntSummary,
      (a, b) => `${a.name || a.sid} ${a.ace_type} ${a.mask.join(",")} → ${b.mask.join(",")}`,
      "inherited",
    ),
  );

  return out;
}

// ── S3 ───────────────────────────────────────────────────────────────────────

function s3OwnerLabel(o: S3Owner | null): string {
  if (!o) return "(none)";
  return o.display_name || o.id;
}

function s3GrantKey(g: S3Grant): string {
  return `${g.grantee_type}\x00${g.grantee_id}\x00${g.permission}`;
}

function s3GrantSummary(g: S3Grant): string {
  const label = g.grantee_name || g.grantee_id;
  return `${g.grantee_type === "canonical_user" ? "user" : g.grantee_type}:${label} ${g.permission}`;
}

function diffS3(prev: S3ACL, curr: S3ACL): ACLDiffItem[] {
  const out: ACLDiffItem[] = [];

  const prevOwner = s3OwnerLabel(prev.owner);
  const currOwner = s3OwnerLabel(curr.owner);
  if (prevOwner !== currOwner) {
    out.push({ kind: "owner_changed", from: prevOwner, to: currOwner });
  }

  const prevByKey = new Map(prev.grants.map((g) => [s3GrantKey(g), g]));
  const currByKey = new Map(curr.grants.map((g) => [s3GrantKey(g), g]));

  for (const g of prev.grants) {
    if (!currByKey.has(s3GrantKey(g))) out.push({ kind: "removed", summary: s3GrantSummary(g) });
  }
  for (const g of curr.grants) {
    if (!prevByKey.has(s3GrantKey(g))) out.push({ kind: "added", summary: s3GrantSummary(g) });
  }

  return out;
}
