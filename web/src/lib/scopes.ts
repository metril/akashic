import type { ScopeInfo } from "../types";

/**
 * Scope helpers shared by the UI gating and the API token dialog. The API
 * (`GET /scopes`) is the authority on what scopes exist; the one rule mirrored
 * here is which scopes a source-limited token may hold (the API refuses the
 * rest with 422), so the dialog can disable them up front.
 */

/** Write scopes a source-limited token may hold; mirrors the API. */
export const SOURCE_LIMITED_WRITE_SCOPES: ReadonlySet<string> = new Set([
  "sources:write",
  "scans:write",
  "tags:write",
]);

export function hasScope(scopes: Iterable<string> | null | undefined, scope: string): boolean {
  if (!scopes) return false;
  for (const s of scopes) if (s === scope) return true;
  return false;
}

/** True when a token limited to sources cannot hold `scope`. */
export function forbiddenWithSourceLimit(scope: string): boolean {
  return (
    (scope.endsWith(":write") || scope === "data:purge" || scope === "tokens:admin") &&
    !SOURCE_LIMITED_WRITE_SCOPES.has(scope)
  );
}

export interface ScopeGroup {
  resource: string;
  scopes: ScopeInfo[];
}

/** Catalog entries the user holds, grouped by resource (the part before `:`), in catalog order. */
export function groupScopes(catalog: ScopeInfo[], available: ReadonlySet<string>): ScopeGroup[] {
  const groups = new Map<string, ScopeInfo[]>();
  for (const info of catalog) {
    if (!available.has(info.name)) continue;
    const resource = info.name.split(":")[0];
    const list = groups.get(resource) ?? [];
    list.push(info);
    groups.set(resource, list);
  }
  return [...groups].map(([resource, scopes]) => ({ resource, scopes }));
}

/** "Read-only": every `:read` scope the user has, plus file content. */
export function readOnlyPreset(available: ReadonlySet<string>): Set<string> {
  const out = new Set<string>();
  for (const s of available) if (s.endsWith(":read") || s === "files:content") out.add(s);
  return out;
}

/** "Full": everything the user has. */
export function fullPreset(available: ReadonlySet<string>): Set<string> {
  return new Set(available);
}

/** Drop the scopes a source-limited token can't hold. */
export function withoutSourceForbidden(selected: ReadonlySet<string>): Set<string> {
  return new Set([...selected].filter((s) => !forbiddenWithSourceLimit(s)));
}

export type ExpiryChoice = "30" | "90" | "365" | "never";

export function expiryToIso(choice: ExpiryChoice, now: Date = new Date()): string | undefined {
  if (choice === "never") return undefined;
  return new Date(now.getTime() + Number(choice) * 86_400_000).toISOString();
}
