import { Navigate } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";

/**
 * Defense-in-depth gate for `/admin/*` routes. The API enforces admin
 * via Depends(require_admin) on every endpoint these pages talk to, so
 * a non-admin who navigates here would just see 403s in the data
 * panels. But rendering the page at all leaks UI affordances and
 * filter chrome that hint at the existence of admin features. Block
 * the route outright and bounce to /dashboard. Sidebar already hides
 * the link for non-admins; this catches direct-URL / bookmark
 * traversal.
 *
 * Loading state: while the /api/v1/users/me query is in flight we render
 * nothing rather than the page-loading spinner, since flashing the
 * admin page for a frame on a slow /me response would defeat the gate.
 */
export default function AdminRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated: authed, user, isAdmin } = useAuth();
  if (!authed) return <Navigate to="/login" replace />;
  if (user === null) return null;
  if (!isAdmin) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}
