import type { ACL, ACLType } from "../types";

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
  // Same type — strategy dispatch added in later tasks.
  return [];
}
