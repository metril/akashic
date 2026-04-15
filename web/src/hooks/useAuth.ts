import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken, clearToken, isAuthenticated } from "../api/client";

export function useAuth() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

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

  const logout = useCallback(() => {
    clearToken();
    navigate("/login");
  }, [navigate]);

  return {
    isAuthenticated: isAuthenticated(),
    login,
    logout,
    loading,
    error,
  };
}
