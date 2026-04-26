import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { Card, Input, Button } from "../components/ui";
import { BrandMark } from "../components/BrandMark";

export default function Login() {
  const { isAuthenticated, login, loading, error } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    login(username, password);
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <Card padding="lg" className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-7">
          <BrandMark className="mb-4" />
          <h1 className="text-xl font-semibold text-gray-900">
            Sign in to Akashic
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Universal file index
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            label="Username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="admin"
            autoComplete="username"
            required
            autoFocus
          />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            autoComplete="current-password"
            required
            error={error || undefined}
          />
          <Button
            type="submit"
            loading={loading}
            className="w-full"
            size="lg"
          >
            {loading ? "Signing in..." : "Sign in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
