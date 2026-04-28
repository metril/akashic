import type { ACL, ACLType, PosixACE, PosixACL } from "../types";

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
