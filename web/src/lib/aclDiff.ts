import type { ACL, ACLType, PosixACE, PosixACL, NfsV4ACE, NfsV4ACL } from "../types";

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
