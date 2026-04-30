import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export interface ResolvedPrincipal {
  sid: string;
  name: string | null;
  domain: string | null;
  kind: string | null;
  status: "resolved" | "unresolved" | "skipped" | "error";
  last_attempt_at: string | null;
}

export type PrincipalMap = Record<string, ResolvedPrincipal>;

const EMPTY: PrincipalMap = {};

/**
 * useResolvePrincipals
 *
 * Fetches a sid → ResolvedPrincipal map for the given source. The
 * caller passes the list of SIDs that need resolution (typically the
 * unresolved-name principals from an entry's ACL); the api batches
 * them through `akashic-scanner resolve-sids` and caches the results.
 *
 * Why this hook only resolves the LIST OF UNRESOLVED SIDs the caller
 * supplies, rather than every SID in an ACL: most ACLs are dominated
 * by well-known SIDs (BUILTIN\Administrators, Authenticated Users,
 * SYSTEM, …) which the scanner already names at scan time. There's no
 * point round-tripping those again. The caller filters down before
 * calling.
 *
 * The query key includes the sorted SID list so different sets of
 * unresolved SIDs across different entries don't collide; that does
 * mean opening two entries with overlapping-but-not-identical sets
 * triggers two requests. That's acceptable — the api cache is hit on
 * the second one anyway.
 */
export function useResolvePrincipals(
  sourceId: string | undefined,
  sids: string[],
): { data: PrincipalMap; isLoading: boolean } {
  const enabled = Boolean(sourceId) && sids.length > 0;
  const sortedSids = [...sids].sort();

  const query = useQuery({
    queryKey: ["principals", "resolve", sourceId, sortedSids],
    queryFn: async () => {
      const resp = await api.resolvePrincipals(sourceId!, sortedSids);
      return resp.resolved;
    },
    enabled,
    // Match the api's POSITIVE_TTL (7 days) for browser-side caching —
    // the api cache is the source of truth and re-issuing the request
    // is cheap (cache hit). 1 hour is a sensible upper bound on
    // browser staleness without re-fetching, since the api will hand
    // back fresh data if its own row went stale.
    staleTime: 60 * 60 * 1000,
  });

  return {
    data: query.data ?? EMPTY,
    isLoading: query.isLoading,
  };
}
