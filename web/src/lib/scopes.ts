import type { ScopeInfo } from "../api/types";

/**
 * Scope helpers shared by the UI gating and the API token dialog. The API
 * (`GET /scopes`) is the authority on what scopes exist and on the rules
 * around them (read-only, allowed with a source limit); nothing is mirrored here.
 */

export function hasScope(scopes: Iterable<string> | null | undefined, scope: string): boolean {
  if (!scopes) return false;
  for (const s of scopes) if (s === scope) return true;
  return false;
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

/** The catalog's per-scope rules, from `GET /scopes` (the API is authoritative). */
export interface ScopeRules {
  /** Scopes a token limited to sources may not hold. */
  limitRefused: ReadonlySet<string>;
  /** Scopes for the "Read-only" preset. */
  readOnly: ReadonlySet<string>;
}

export function scopeRules(catalog: ScopeInfo[]): ScopeRules {
  return {
    limitRefused: new Set(catalog.filter((s) => !s.allowed_with_source_limit).map((s) => s.name)),
    readOnly: new Set(catalog.filter((s) => s.read_only).map((s) => s.name)),
  };
}

/** "Read-only": the read-only scopes the user has. */
export function readOnlyPreset(available: ReadonlySet<string>, rules: ScopeRules): Set<string> {
  return new Set([...available].filter((s) => rules.readOnly.has(s)));
}

/** "Full": everything the user has. */
export function fullPreset(available: ReadonlySet<string>): Set<string> {
  return new Set(available);
}

/** Drop the scopes a source-limited token can't hold. */
export function withoutSourceForbidden(selected: ReadonlySet<string>, rules: ScopeRules): Set<string> {
  return new Set([...selected].filter((s) => !rules.limitRefused.has(s)));
}

export type ExpiryChoice = "30" | "90" | "365" | "never";

export function expiryToIso(choice: ExpiryChoice, now: Date = new Date()): string | undefined {
  if (choice === "never") return undefined;
  return new Date(now.getTime() + Number(choice) * 86_400_000).toISOString();
}
