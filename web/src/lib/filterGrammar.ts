/**
 * Predicate language shared with the api (api/akashic/services/filter_grammar.py).
 *
 * URL form is `?filters=<base64url(json)>` — base64url avoids escape bugs
 * when values contain slashes, colons, or dots (paths and SIDs both have
 * those). Keep this file in lockstep with the Python module: adding a
 * predicate kind on one side without the other means stale URLs silently
 * drop the new kind on the side that doesn't know it.
 *
 * Mismatched/unknown predicates from a stale or hand-edited URL are
 * dropped on deserialize() rather than thrown — a bad ?filters= shouldn't
 * blank-page the user.
 */

export type Right = "read" | "write" | "delete";

export type Predicate =
  | { kind: "extension"; value: string }
  | { kind: "source"; value: string }
  | { kind: "owner"; value: string }
  | { kind: "principal"; value: string; right?: Right }
  | { kind: "mime"; value: string }
  | { kind: "size"; op: "gte" | "lte" | "eq"; value: number }
  | { kind: "mtime"; op: "gte" | "lte"; value: string }
  | { kind: "path"; value: string }
  | { kind: "tag"; value: string };

// ── Encoding ──────────────────────────────────────────────────────────────

function utf8ToBase64Url(s: string): string {
  // btoa() chokes on non-Latin1, so go through TextEncoder first.
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToUtf8(s: string): string {
  const padded = s.replace(/-/g, "+").replace(/_/g, "/")
    + "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(padded);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

// ── Validation ────────────────────────────────────────────────────────────

const RIGHTS: ReadonlySet<Right> = new Set(["read", "write", "delete"]);
const SIZE_OPS = new Set(["gte", "lte", "eq"]);
const MTIME_OPS = new Set(["gte", "lte"]);

function isPredicate(p: unknown): p is Predicate {
  if (typeof p !== "object" || p === null) return false;
  const o = p as Record<string, unknown>;
  switch (o.kind) {
    case "extension":
    case "source":
    case "owner":
    case "mime":
    case "path":
    case "tag":
      return typeof o.value === "string";
    case "principal":
      return (
        typeof o.value === "string"
        && (o.right === undefined || (typeof o.right === "string" && RIGHTS.has(o.right as Right)))
      );
    case "size":
      return typeof o.value === "number" && typeof o.op === "string" && SIZE_OPS.has(o.op);
    case "mtime":
      return typeof o.value === "string" && typeof o.op === "string" && MTIME_OPS.has(o.op);
    default:
      return false;
  }
}

// ── Public API ────────────────────────────────────────────────────────────

export function serialize(preds: Predicate[]): string {
  if (preds.length === 0) return "";
  return utf8ToBase64Url(JSON.stringify(preds));
}

export function deserialize(s: string | null | undefined): Predicate[] {
  if (!s) return [];
  try {
    const parsed = JSON.parse(base64UrlToUtf8(s));
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isPredicate);
  } catch {
    return [];
  }
}

/** Convenience: build a predicate-equality predicate without the kind boilerplate. */
export function pred(p: Predicate): Predicate {
  return p;
}

/** Two predicates point at the same column+value (so chip dedup can ignore one)?
 * Used by the chip UI so clicking the same owner cell twice doesn't stack
 * a duplicate predicate on the URL.
 */
export function sameTarget(a: Predicate, b: Predicate): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "size" && b.kind === "size") return a.op === b.op;
  if (a.kind === "mtime" && b.kind === "mtime") return a.op === b.op;
  if (a.kind === "principal" && b.kind === "principal") {
    return a.value === b.value && (a.right ?? "read") === (b.right ?? "read");
  }
  return (a as { value: string }).value === (b as { value: string }).value;
}
