import { useMemo } from "react";
import { useAuth } from "./useAuth";
import { hasScope } from "../lib/scopes";

/**
 * The caller's effective scopes from /users/me. UI gating goes through this
 * (never the role), the same way the API authorises: by scope. `ready` is
 * false until /users/me has loaded.
 */
export function useScopes() {
  const { user } = useAuth();
  return useMemo(() => {
    const scopes = new Set(user?.scopes ?? []);
    return {
      scopes,
      ready: user !== null,
      has: (scope: string) => hasScope(scopes, scope),
    };
  }, [user]);
}
