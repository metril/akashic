import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, setToken, clearToken, isAuthenticated } from "../api/client";

interface CurrentUser {
  id: string;
  username: string;
  email: string | null;
  role: string;
}

/**
 * `useAuth` exposes the user's role via a /api/users/me lookup. The role
 * lives on the server-side User row and isn't encoded in the JWT, so
 * client-side gating that depends on role (e.g., showing the Edit
 * button on a source) MUST go through this hook rather than decoding
 * the token.
 *
 * The query is gated on `isAuthenticated` so logged-out states don't
 * fire a 401 in the background.
 */
export function useAuth() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const authed = isAuthenticated();

  const meQuery = useQuery<CurrentUser>({
    queryKey: ["users", "me"],
    queryFn: () => api.get<CurrentUser>("/users/me"),
    enabled: authed,
    staleTime: 5 * 60 * 1000,
  });

  const login = useCallback(
    async (username: string, password: string) => {
      setLoading(true);
      setError(null);
      try {
        const response = await api.login(username, password);
        setToken(response.access_token);
        navigate("/dashboard");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Login failed");
      } finally {
        setLoading(false);
      }
    },
    [navigate]
  );

  const logout = useCallback(async () => {
    // Revoke the refresh chain before dropping the access token, so a
    // future tab that still has the cookie can't extend a session the
    // user explicitly ended. Errors are swallowed — local logout
    // happens regardless.
    await api.logoutServer();
    clearToken();
    navigate("/login");
  }, [navigate]);

  return {
    isAuthenticated: authed,
    user: meQuery.data ?? null,
    role: meQuery.data?.role ?? null,
    isAdmin: meQuery.data?.role === "admin",
    login,
    logout,
    loading,
    error,
  };
}
