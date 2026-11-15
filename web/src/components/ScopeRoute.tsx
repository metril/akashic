import { Navigate } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";
import { useScopes } from "../hooks/useScopes";

/**
 * Defense-in-depth gate for routes that need a scope. The API enforces the
 * scope on every endpoint these pages talk to, so a user without it would
 * only see 403s in the data panels; rendering the page at all would still
 * leak UI affordances. Block the route and bounce to /dashboard. The sidebar
 * and settings tabs hide the link; this catches direct-URL traversal.
 *
 * Loading state: while /users/me is in flight we render nothing rather than
 * the page, since flashing a gated page for a frame would defeat the gate.
 */
export default function ScopeRoute({
  scope,
  children,
}: {
  scope: string;
  children: React.ReactNode;
}) {
  const { isAuthenticated: authed, user } = useAuth();
  const { has } = useScopes();
  if (!authed) return <Navigate to="/login" replace />;
  if (user === null) return null;
  if (!has(scope)) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}
