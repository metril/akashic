const API_BASE = "/api";
const TOKEN_KEY = "akashic_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  skipAuth?: boolean;
  /** Internal — set by the silent-refresh path to prevent infinite recursion. */
  _retryAfterRefresh?: boolean;
}

// In-flight refresh promise so a burst of concurrent 401s collapses
// into one /api/auth/refresh call. Without this, opening the app to a
// page that fires N parallel queries with a stale token would mint N
// new chains and revoke N-1 of them as replays.
let inflightRefresh: Promise<string | null> | null = null;

async function silentRefresh(): Promise<string | null> {
  if (!inflightRefresh) {
    inflightRefresh = (async () => {
      try {
        // The refresh cookie rides on this fetch automatically because
        // it's HttpOnly + Path=/api/auth + same-origin.
        const r = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });
        if (!r.ok) return null;
        const body = (await r.json()) as { access_token?: string };
        if (!body.access_token) return null;
        setToken(body.access_token);
        return body.access_token;
      } catch {
        return null;
      } finally {
        // Clear the cache once this attempt resolves — next 401 starts fresh.
        setTimeout(() => {
          inflightRefresh = null;
        }, 0);
      }
    })();
  }
  return inflightRefresh;
}

async function request<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const { method = "GET", body, skipAuth = false, _retryAfterRefresh = false } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (!skipAuth) {
    const token = getToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    // credentials:include lets the refresh cookie ride on the same
    // origin — no effect on cross-origin since the SPA + API share a
    // host in normal deployments.
    credentials: "include",
  });

  if (response.status === 401) {
    // Silent refresh: swap stale access token for a fresh one and
    // retry the original request once. Skip when:
    //   - skipAuth=true (login/register/refresh themselves)
    //   - _retryAfterRefresh=true (we already retried — give up)
    //   - the path is /auth/refresh (avoid loop)
    if (!skipAuth && !_retryAfterRefresh && !path.startsWith("/auth/refresh")) {
      const newToken = await silentRefresh();
      if (newToken) {
        return request<T>(path, { ...options, _retryAfterRefresh: true });
      }
    }
    clearToken();
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    let errorMessage = `HTTP error ${response.status}`;
    try {
      const errorData = await response.json();
      const detail = errorData.detail;
      if (typeof detail === "string") {
        errorMessage = detail;
      } else if (detail && typeof detail === "object") {
        errorMessage = (detail as { message?: string }).message ?? JSON.stringify(detail);
      } else if (errorData.message) {
        errorMessage = errorData.message;
      }
    } catch {
      // ignore JSON parse errors
    }
    throw new Error(errorMessage);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export const api = {
  get<T>(path: string): Promise<T> {
    return request<T>(path);
  },

  post<T>(path: string, body?: unknown, skipAuth = false): Promise<T> {
    return request<T>(path, { method: "POST", body, skipAuth });
  },

  patch<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, { method: "PATCH", body });
  },

  delete<T>(path: string): Promise<T> {
    return request<T>(path, { method: "DELETE" });
  },

  // Auth endpoints
  login(username: string, password: string) {
    return request<{ access_token: string; token_type: string }>(
      "/users/login",
      {
        method: "POST",
        body: { username, password },
        skipAuth: true,
      }
    );
  },

  register(username: string, email: string, password: string) {
    return request<{ id: number; username: string; email: string }>(
      "/users/register",
      {
        method: "POST",
        body: { username, email, password },
        skipAuth: true,
      }
    );
  },

  /** Hit the server logout endpoint to revoke the refresh cookie. The
   *  short-lived access JWT keeps working until its TTL expires; this
   *  only kills the long-lived refresh chain. */
  async logoutServer() {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // Network failure during logout shouldn't block the local clear —
      // the user wants out, regardless.
    }
  },

  cancelScan(scanId: string) {
    return request<{ scan_id: string; status: string }>(
      `/scans/${scanId}/cancel`,
      { method: "POST" }
    );
  },

  // PR2 — on-demand SID resolution. NtACL renderer calls this for the
  // SIDs in an entry's ACL that the scanner couldn't translate at
  // scan time (DC unreachable, etc.). The api caches per (source,
  // sid) so repeat opens are free; first open is one round-trip plus
  // an LSARPC call from the scanner host.
  resolvePrincipals(sourceId: string, sids: string[]) {
    return request<{
      resolved: Record<
        string,
        {
          sid: string;
          name: string | null;
          domain: string | null;
          kind: string | null;
          status: "resolved" | "unresolved" | "skipped" | "error";
          last_attempt_at: string | null;
        }
      >;
    }>(`/principals/resolve`, {
      method: "POST",
      body: { source_id: sourceId, sids },
    });
  },

  // setup_required is true on a fresh deployment with zero users — the
  // login page uses this to flip into "create the admin account" mode
  // instead of showing a dead-end sign-in form.
  getProviders() {
    return request<{
      local: boolean;
      oidc: boolean;
      ldap: boolean;
      setup_required: boolean;
    }>("/auth/providers", { skipAuth: true });
  },

  me() {
    return request<{ id: number; username: string; email: string }>("/users/me");
  },

  // Phase 2 — bulk delete in Duplicates. Returns per-entry success/failure
  // so the UI can mark which copies survived vs. failed (permission
  // denied is the common case).
  deleteDuplicateCopies(
    contentHash: string,
    keepEntryId: string,
    deleteEntryIds: string[],
  ) {
    return request<{
      deleted: { entry_id: string; path: string; ok: boolean }[];
      failed: { entry_id: string; path: string; ok: boolean; step: string; message: string }[];
    }>(`/duplicates/${contentHash}/delete-copies`, {
      method: "POST",
      body: { keep_entry_id: keepEntryId, delete_entry_ids: deleteEntryIds },
    });
  },
};

export default api;
