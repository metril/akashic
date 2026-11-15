import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

export const API_BASE = "/api/v1";
export const WS_BASE = "/ws/v1";

// Access JWT lives in memory only (review W-C1). A stored JWT in
// localStorage is readable by any same-origin script — including any
// future XSS, a misconfigured CSP, or a compromised npm package — and
// can be replayed verbatim against the API. The HttpOnly refresh
// cookie is still the source of truth for "is the user logged in";
// the access token is short-lived and re-minted from that cookie via
// silent refresh.
//
// SESSION_HINT_KEY persists ONLY a boolean flag ("1" or absent) so we
// know whether to attempt silent refresh on cold start. The flag is
// not a credential — losing it just means an extra round-trip. It
// also lets isAuthenticated() return true on a fresh page load before
// the silent-refresh has had a chance to populate _accessToken.
const SESSION_HINT_KEY = "akashic_session_present";

let _accessToken: string | null = null;

export function getToken(): string | null {
  return _accessToken;
}

export function setToken(token: string): void {
  _accessToken = token;
  try {
    localStorage.setItem(SESSION_HINT_KEY, "1");
  } catch {
    // Private browsing / quota — losing the hint just means an extra
    // refresh round-trip on next cold load.
  }
}

export function clearToken(): void {
  _accessToken = null;
  try {
    localStorage.removeItem(SESSION_HINT_KEY);
  } catch {
    /* ignore */
  }
}

export function hasSessionHint(): boolean {
  try {
    return localStorage.getItem(SESSION_HINT_KEY) === "1";
  } catch {
    return false;
  }
}

export function isAuthenticated(): boolean {
  // Optimistic: in-memory token OR a session hint counts. The hint
  // case will trigger a silent-refresh on the first authenticated
  // request and either succeed (mints _accessToken) or 401 → clear.
  return _accessToken !== null || hasSessionHint();
}

/** Mirrors the API's ErrorResponse body: {code, message, detail?, request_id}. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly detail: unknown,
    public readonly requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function parseApiError(status: number, body: unknown): ApiError {
  const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  return new ApiError(
    status,
    typeof b.code === "string" ? b.code : `http_${status}`,
    typeof b.message === "string" ? b.message : `HTTP error ${status}`,
    b.detail ?? null,
    typeof b.request_id === "string" ? b.request_id : null,
  );
}

// In-flight refresh promise so a burst of concurrent 401s collapses
// into one /api/v1/auth/refresh call. Without this, opening the app to a
// page that fires N parallel queries with a stale token would mint N
// new chains and revoke N-1 of them as replays.
let inflightRefresh: Promise<string | null> | null = null;

async function silentRefresh(): Promise<string | null> {
  if (!inflightRefresh) {
    const doRefresh = async (): Promise<string | null> => {
      try {
        // The refresh cookie rides on this fetch automatically because
        // it's HttpOnly + Path=/api/v1/auth + same-origin.
        const r = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });
        if (!r.ok) {
          // Refresh chain is dead (cookie expired, replayed, revoked).
          // Clear the session-hint flag too — otherwise isAuthenticated()
          // keeps returning true on cold load, bootstrap retries the
          // refresh, gets 401, and the user enters a redirect loop. The
          // hint must reflect "do we have a viable session," not just
          // "did we ever have one."
          clearToken();
          return null;
        }
        const body = (await r.json()) as { access_token?: string };
        if (!body.access_token) {
          clearToken();
          return null;
        }
        setToken(body.access_token);
        return body.access_token;
      } catch {
        // Network/parse error — leave the hint intact so the next attempt
        // can try again. (A transient outage shouldn't log the user out.)
        return null;
      } finally {
        // Clear the cache once this attempt resolves — next 401 starts fresh.
        setTimeout(() => {
          inflightRefresh = null;
        }, 0);
      }
    };
    // Serialize across tabs: the server rotates the refresh cookie on
    // each call, so two tabs refreshing at once would race the rotation.
    // Fall back to an unlocked refresh where Web Locks is unavailable.
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    inflightRefresh = locks
      ? (locks.request("akashic-refresh", doRefresh) as unknown as Promise<string | null>)
      : doRefresh();
  }
  return inflightRefresh;
}

/** Finish an SSO login: the OIDC callback set the refresh cookie; trade
 *  it for an access token. */
export async function completeSsoLogin(): Promise<boolean> {
  return (await silentRefresh()) !== null;
}

/** Cold-start auth bootstrap: if the session hint is set but no
 *  in-memory token exists, attempt one silent refresh so authed
 *  routes can render with valid credentials before any user-facing
 *  request fires. Returns true if the user is authenticated after
 *  the attempt. */
export async function bootstrapAuth(): Promise<boolean> {
  if (_accessToken) return true;
  if (!hasSessionHint()) return false;
  const tok = await silentRefresh();
  return tok !== null;
}

/** Build /login?next=<current-path>. Validates that `next` starts
 *  with "/" so an attacker can't smuggle an absolute URL through. */
function loginUrlWithNext(): string {
  const here = window.location.pathname + window.location.search;
  if (!here.startsWith("/") || here.startsWith("//") || here === "/login") {
    return "/login";
  }
  return `/login?next=${encodeURIComponent(here)}`;
}

/** Endpoints that authenticate themselves: no bearer header, no silent refresh. */
const SKIP_AUTH_PATHS = new Set(
  ["/users/login", "/users/register", "/auth/providers", "/auth/refresh"].map((p) => API_BASE + p),
);

// Request must be absolute for the Fetch API's Request constructor.
const ORIGIN = typeof window !== "undefined" ? window.location.origin : "http://localhost";

// Clone of each in-flight request, keyed by openapi-fetch's request id, so
// a 401 can replay it (the original's body is consumed by the first send).
const pending = new Map<string, Request>();

const authMiddleware: Middleware = {
  onRequest({ request, id }) {
    if (SKIP_AUTH_PATHS.has(new URL(request.url).pathname)) return request;
    pending.set(id, request.clone());
    const token = getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },

  async onResponse({ response, id }) {
    const retryable = pending.get(id);
    pending.delete(id);

    if (response.status === 401) {
      // Silent refresh: swap the stale access token for a fresh one and
      // replay the request once. Skipped for the self-authenticating
      // endpoints (login/register/refresh) and when the refresh fails.
      if (retryable) {
        const newToken = await silentRefresh();
        if (newToken) {
          retryable.headers.set("Authorization", `Bearer ${newToken}`);
          const retried = await fetch(retryable);
          if (retried.status !== 401) return checked(retried);
        }
      }
      clearToken();
      if (window.location.pathname !== "/login") {
        // ?next= preserves the current destination so post-login lands
        // back where the user was, not /dashboard (review W-C2).
        window.location.href = loginUrlWithNext();
      }
      throw new Error("Unauthorized");
    }
    return checked(response);
  },
};

/** Throw the ErrorResponse envelope as ApiError for any non-2xx response. */
async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // non-JSON error body (proxy error page, etc.)
  }
  throw parseApiError(response.status, body);
}

const clientOptions = {
  baseUrl: ORIGIN,
  // credentials:include lets the refresh cookie ride on the same origin.
  credentials: "include" as const,
  // Resolve global fetch per call, not at module load (tests stub it).
  fetch: (request: Request) => fetch(request),
};

/** Typed client generated from openapi.json. Paths carry the /api/v1 prefix.
 *  Non-2xx responses throw ApiError, so `data` is the success body. */
export const client = createClient<paths>(clientOptions);
client.use(authMiddleware);

/** The success body of a typed call. The middleware throws ApiError on any
 *  non-2xx response, so `data` is always present for bodies that have one. */
export function ok<T>(result: { data?: T }): T {
  return result.data as T;
}

export const api = {
  login: async (username: string, password: string) =>
    (await client.POST("/api/v1/users/login", { body: { username, password } })).data!,

  register: async (username: string, email: string, password: string) =>
    (await client.POST("/api/v1/users/register", { body: { username, email, password } })).data!,

  /** Hit the server logout endpoint to revoke the refresh cookie. The
   *  short-lived access JWT keeps working until its TTL expires; this
   *  only kills the long-lived refresh chain. */
  async logoutServer() {
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
    } catch {
      // Network failure during logout shouldn't block the local clear —
      // the user wants out, regardless.
    }
  },

  cancelScan: async (scanId: string) =>
    (await client.POST("/api/v1/scans/{scan_id}/cancel", { params: { path: { scan_id: scanId } } })).data!,

  // On-demand SID resolution for the NtACL renderer; the api caches per
  // (source, sid).
  resolvePrincipals: async (sourceId: string, sids: string[]) =>
    (await client.POST("/api/v1/principals/resolve", { body: { source_id: sourceId, sids } })).data!,

  // setup_required is true on a fresh deployment with zero users — the
  // login page uses this to flip into "create the admin account" mode.
  getProviders: async () => (await client.GET("/api/v1/auth/providers")).data!,

  me: async () => (await client.GET("/api/v1/users/me")).data!,

  // Bulk delete in Duplicates; per-entry outcomes let the UI mark which
  // copies survived (permission denied is the common case).
  deleteDuplicateCopies: async (contentHash: string, keepEntryId: string, deleteEntryIds: string[]) =>
    (
      await client.POST("/api/v1/duplicates/{content_hash}/delete-copies", {
        params: { path: { content_hash: contentHash } },
        body: { keep_entry_id: keepEntryId, delete_entry_ids: deleteEntryIds },
      })
    ).data!,
};

export default api;
