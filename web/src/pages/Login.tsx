import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { Card, Input, Button } from "../components/ui";
import { BrandMark } from "../components/BrandMark";
import { api } from "../api/client";

// Mirror api/akashic/schemas/user.py — 12-char floor agrees with the
// bcrypt-72-byte upper bound on the API and gives an admin password
// that costs more than a coffee break to brute force. The API rejects
// shorter passwords too; this just surfaces the rule before submit.
const MIN_PASSWORD_LENGTH = 12;

type Providers = {
  local: boolean;
  oidc: boolean;
  ldap: boolean;
  setup_required: boolean;
};

export default function Login() {
  const { isAuthenticated, login, loading, error } = useAuth();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);

  // Sign-in form state.
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // Bootstrap-form state. Kept separate from the sign-in fields so that
  // a user who started filling one and then refreshed (flipping the
  // page mode) doesn't see stale fields from the other.
  const [setupUsername, setSetupUsername] = useState("");
  const [setupEmail, setSetupEmail] = useState("");
  const [setupPassword, setSetupPassword] = useState("");
  const [setupConfirm, setSetupConfirm] = useState("");
  const [setupSubmitting, setSetupSubmitting] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getProviders()
      .then((p) => {
        if (!cancelled) setProviders(p);
      })
      .catch((err) => {
        if (!cancelled) {
          setProvidersError(
            err instanceof Error ? err.message : "Could not reach the server",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    login(username, password);
  }

  async function handleSetupSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSetupError(null);
    if (setupPassword.length < MIN_PASSWORD_LENGTH) {
      setSetupError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (setupPassword !== setupConfirm) {
      setSetupError("Passwords don't match.");
      return;
    }
    setSetupSubmitting(true);
    try {
      await api.register(setupUsername, setupEmail, setupPassword);
      // Auto-login so the user lands on /dashboard without a second
      // form. login() handles its own error state, but if it fails
      // here we still surface a hint — the account exists, the user
      // just needs to refresh and sign in normally.
      await login(setupUsername, setupPassword);
    } catch (err) {
      setSetupError(err instanceof Error ? err.message : "Setup failed");
      // Re-fetch providers so a "registration closed" race (another
      // tab beat us) flips the page back to the sign-in form on the
      // next render.
      api.getProviders().then(setProviders).catch(() => {});
    } finally {
      setSetupSubmitting(false);
    }
  }

  // Loading: provider discovery hasn't returned yet. Render the same
  // brand chrome so we don't get a flash-of-empty-page.
  if (!providers && !providersError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-app px-4">
        <Card padding="lg" className="w-full max-w-sm">
          <div className="flex flex-col items-center mb-7">
            <BrandMark className="mb-4" />
            <p className="text-sm text-fg-muted">Loading…</p>
          </div>
        </Card>
      </div>
    );
  }

  if (providersError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-app px-4">
        <Card padding="lg" className="w-full max-w-sm">
          <div className="flex flex-col items-center mb-4">
            <BrandMark className="mb-4" />
            <h1 className="text-xl font-semibold text-fg">Akashic</h1>
          </div>
          <p className="text-sm text-red-600 mt-2">
            Couldn't reach the API: {providersError}
          </p>
        </Card>
      </div>
    );
  }

  // From here on `providers` is guaranteed non-null.
  const setupRequired = providers?.setup_required === true;

  if (setupRequired) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-app px-4">
        <Card padding="lg" className="w-full max-w-sm">
          <div className="flex flex-col items-center mb-7">
            <BrandMark className="mb-4" />
            <h1 className="text-xl font-semibold text-fg">
              Welcome to Akashic
            </h1>
            <p className="text-sm text-fg-muted mt-1 text-center">
              No accounts exist yet. Create the admin account to get started.
            </p>
          </div>

          <form onSubmit={handleSetupSubmit} className="space-y-4">
            <Input
              label="Username"
              type="text"
              value={setupUsername}
              onChange={(e) => setSetupUsername(e.target.value)}
              placeholder="admin"
              autoComplete="username"
              required
              autoFocus
            />
            <Input
              label="Email"
              type="email"
              value={setupEmail}
              onChange={(e) => setSetupEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              required
            />
            <Input
              label="Password"
              type="password"
              value={setupPassword}
              onChange={(e) => setSetupPassword(e.target.value)}
              placeholder={`At least ${MIN_PASSWORD_LENGTH} characters`}
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
            />
            <Input
              label="Confirm password"
              type="password"
              value={setupConfirm}
              onChange={(e) => setSetupConfirm(e.target.value)}
              placeholder="Type it again"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              error={setupError || undefined}
            />
            <Button
              type="submit"
              loading={setupSubmitting || loading}
              className="w-full"
              size="lg"
            >
              {setupSubmitting || loading
                ? "Creating account…"
                : "Create admin account"}
            </Button>
          </form>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-app px-4">
      <Card padding="lg" className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-7">
          <BrandMark className="mb-4" />
          <h1 className="text-xl font-semibold text-fg">
            Sign in to Akashic
          </h1>
          <p className="text-sm text-fg-muted mt-1">
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
