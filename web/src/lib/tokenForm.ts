import {
  expiryToIso,
  fullPreset,
  readOnlyPreset,
  withoutSourceForbidden,
  type ExpiryChoice,
  type ScopeRules,
} from "./scopes";
import type { ApiTokenCreate } from "../api/types";

export interface TokenFormState {
  name: string;
  scopes: Set<string>;
  /** Empty means every source. */
  sourceIds: string[];
  expiry: ExpiryChoice;
}

export type TokenFormAction =
  | { type: "name"; name: string }
  | { type: "expiry"; expiry: ExpiryChoice }
  | { type: "toggle"; scope: string; rules: ScopeRules }
  | { type: "preset"; preset: "read-only" | "full"; available: ReadonlySet<string>; rules: ScopeRules }
  | { type: "sources"; sourceIds: string[]; rules: ScopeRules }
  /** The catalog rules arrived or changed: re-clean the selection. */
  | { type: "rules"; rules: ScopeRules };

export const initialTokenForm: TokenFormState = {
  name: "",
  scopes: new Set(),
  sourceIds: [],
  expiry: "90",
};

export const isLimited = (s: TokenFormState) => s.sourceIds.length > 0;

/**
 * Selecting sources clears the scopes a source-limited token can't hold (the
 * API would 422 them), and those scopes can't be picked again until the
 * limit is removed.
 */
export function tokenFormReducer(state: TokenFormState, a: TokenFormAction): TokenFormState {
  switch (a.type) {
    case "name":
      return { ...state, name: a.name };
    case "expiry":
      return { ...state, expiry: a.expiry };
    case "toggle": {
      if (isLimited(state) && a.rules.limitRefused.has(a.scope)) return state;
      const scopes = new Set(state.scopes);
      if (!scopes.delete(a.scope)) scopes.add(a.scope);
      return { ...state, scopes };
    }
    case "preset": {
      const base = a.preset === "full" ? fullPreset(a.available) : readOnlyPreset(a.available, a.rules);
      return { ...state, scopes: isLimited(state) ? withoutSourceForbidden(base, a.rules) : base };
    }
    case "rules":
      return isLimited(state) ? { ...state, scopes: withoutSourceForbidden(state.scopes, a.rules) } : state;
    case "sources":
      return {
        ...state,
        sourceIds: a.sourceIds,
        scopes: a.sourceIds.length > 0 ? withoutSourceForbidden(state.scopes, a.rules) : state.scopes,
      };
  }
}

export const canSubmitTokenForm = (s: TokenFormState) => s.name.trim().length > 0 && s.scopes.size > 0;

export function toCreateRequest(s: TokenFormState, now?: Date): ApiTokenCreate {
  const body: ApiTokenCreate = { name: s.name.trim(), scopes: [...s.scopes].sort() };
  if (s.sourceIds.length > 0) body.source_ids = s.sourceIds;
  const expires = expiryToIso(s.expiry, now);
  if (expires) body.expires_at = expires;
  return body;
}
