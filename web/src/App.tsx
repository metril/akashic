import { lazy, Suspense, useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { bootstrapAuth, isAuthenticated } from "./api/client";
import { useAuth } from "./hooks/useAuth";
import Layout from "./components/Layout";
import { ErrorBoundary, Spinner } from "./components/ui";
import Login from "./pages/Login";

// Lazy-load every authenticated page so the initial bundle ships only the
// Login + Layout shell + the React Query / Router runtime. Each page chunk
// is loaded on first navigation; route changes within a session reuse the
// already-fetched chunk.
const Dashboard          = lazy(() => import("./pages/Dashboard"));
const Browse             = lazy(() => import("./pages/Browse"));
const Search             = lazy(() => import("./pages/Search"));
const Sources            = lazy(() => import("./pages/Sources"));
const Hosts              = lazy(() => import("./pages/Hosts"));
const Duplicates         = lazy(() => import("./pages/Duplicates"));
const Analytics          = lazy(() => import("./pages/Analytics"));
const StorageExplorer    = lazy(() => import("./pages/StorageExplorer"));
const SettingsLayout     = lazy(() => import("./components/settings/SettingsLayout"));
const SettingsIdentities = lazy(() => import("./pages/SettingsIdentities"));
const SettingsTags       = lazy(() => import("./pages/SettingsTags"));
const SettingsSchedules  = lazy(() => import("./pages/SettingsSchedules"));
const SettingsScanners   = lazy(() => import("./pages/SettingsScanners"));
const SettingsCredentials = lazy(() => import("./pages/SettingsCredentials"));
const SettingsOAuth      = lazy(() => import("./pages/SettingsOAuth"));
const AdminAudit         = lazy(() => import("./pages/AdminAudit"));
const AdminAccess        = lazy(() => import("./pages/AdminAccess"));

function PrivateRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) {
    // Preserve the destination across the login bounce so the user
    // lands back where they were after signing in.
    const here = window.location.pathname + window.location.search;
    const next =
      here.startsWith("/") && !here.startsWith("//") && here !== "/login"
        ? `?next=${encodeURIComponent(here)}`
        : "";
    return <Navigate to={`/login${next}`} replace />;
  }
  return <>{children}</>;
}

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
 * Loading state: while the /api/users/me query is in flight we render
 * nothing rather than the page-loading spinner, since flashing the
 * admin page for a frame on a slow /me response would defeat the gate.
 */
function AdminRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated: authed, user, isAdmin } = useAuth();
  if (!authed) return <Navigate to="/login" replace />;
  if (user === null) return null;
  if (!isAdmin) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-24 text-fg-subtle">
      <Spinner />
    </div>
  );
}

export default function App() {
  // Cold-start auth bootstrap: if a session hint is present in
  // localStorage but we have no in-memory access token (the access
  // token lives in memory only — review W-C1), kick off one silent
  // refresh before rendering the routes so authed routes don't bounce
  // to /login mid-render. While the bootstrap is in flight we render
  // a top-level spinner.
  const [bootDone, setBootDone] = useState(false);
  useEffect(() => {
    let cancelled = false;
    bootstrapAuth().finally(() => {
      if (!cancelled) setBootDone(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!bootDone) {
    return (
      <div className="flex items-center justify-center h-screen text-fg-subtle">
        <Spinner />
      </div>
    );
  }

  return (
    <>
      <Toaster
        position="bottom-right"
        // Pull theme from <html class="dark"> via "system" — sonner reads
        // the class on render. The useTheme hook keeps that class in sync.
        theme="system"
        // v0.5.6: richColors paints success=green, error=rose, warning=amber,
        // info=blue. Without it, the default neutral palette renders dark
        // text on a dark background in dark mode (the "black toast" report).
        richColors
        closeButton
        toastOptions={{ className: "rounded-lg" }}
      />
      <Routes>
        <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <PrivateRoute>
            <ErrorBoundary>
              <Layout />
            </ErrorBoundary>
          </PrivateRoute>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route
          path="dashboard"
          element={
            <Suspense fallback={<PageLoader />}>
              <Dashboard />
            </Suspense>
          }
        />
        <Route
          path="browse"
          element={
            <Suspense fallback={<PageLoader />}>
              <Browse />
            </Suspense>
          }
        />
        <Route
          path="search"
          element={
            <Suspense fallback={<PageLoader />}>
              <Search />
            </Suspense>
          }
        />
        <Route
          path="sources"
          element={
            <Suspense fallback={<PageLoader />}>
              <Sources />
            </Suspense>
          }
        />
        <Route
          path="hosts"
          element={
            <Suspense fallback={<PageLoader />}>
              <Hosts />
            </Suspense>
          }
        />
        <Route
          path="duplicates"
          element={
            <Suspense fallback={<PageLoader />}>
              <Duplicates />
            </Suspense>
          }
        />
        <Route
          path="analytics"
          element={
            <Suspense fallback={<PageLoader />}>
              <Analytics />
            </Suspense>
          }
        />
        <Route
          path="storage"
          element={
            <Suspense fallback={<PageLoader />}>
              <StorageExplorer />
            </Suspense>
          }
        />
        <Route
          path="settings"
          element={
            <Suspense fallback={<PageLoader />}>
              <SettingsLayout />
            </Suspense>
          }
        >
          <Route index element={<Navigate to="identities" replace />} />
          <Route
            path="identities"
            element={
              <Suspense fallback={<PageLoader />}>
                <SettingsIdentities />
              </Suspense>
            }
          />
          <Route
            path="tags"
            element={
              <Suspense fallback={<PageLoader />}>
                <SettingsTags />
              </Suspense>
            }
          />
          <Route
            path="schedules"
            element={
              <Suspense fallback={<PageLoader />}>
                <SettingsSchedules />
              </Suspense>
            }
          />
          <Route
            path="scanners"
            element={
              <Suspense fallback={<PageLoader />}>
                <SettingsScanners />
              </Suspense>
            }
          />
          <Route
            path="credentials"
            element={
              <Suspense fallback={<PageLoader />}>
                <SettingsCredentials />
              </Suspense>
            }
          />
          <Route
            path="oauth"
            element={
              <Suspense fallback={<PageLoader />}>
                <SettingsOAuth />
              </Suspense>
            }
          />
        </Route>
        <Route
          path="admin/audit"
          element={
            <AdminRoute>
              <Suspense fallback={<PageLoader />}>
                <AdminAudit />
              </Suspense>
            </AdminRoute>
          }
        />
        <Route
          path="admin/access"
          element={
            <AdminRoute>
              <Suspense fallback={<PageLoader />}>
                <AdminAccess />
              </Suspense>
            </AdminRoute>
          }
        />
      </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </>
  );
}
