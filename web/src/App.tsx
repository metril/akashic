import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { isAuthenticated } from "./api/client";
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
const Duplicates         = lazy(() => import("./pages/Duplicates"));
const Analytics          = lazy(() => import("./pages/Analytics"));
const Settings           = lazy(() => import("./pages/Settings"));
const SettingsIdentities = lazy(() => import("./pages/SettingsIdentities"));
const AdminAudit         = lazy(() => import("./pages/AdminAudit"));

function PrivateRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-24 text-gray-400">
      <Spinner />
    </div>
  );
}

export default function App() {
  return (
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
        <Route path="settings">
          <Route
            index
            element={
              <Suspense fallback={<PageLoader />}>
                <Settings />
              </Suspense>
            }
          />
          <Route
            path="identities"
            element={
              <Suspense fallback={<PageLoader />}>
                <SettingsIdentities />
              </Suspense>
            }
          />
        </Route>
        <Route
          path="admin/audit"
          element={
            <Suspense fallback={<PageLoader />}>
              <AdminAudit />
            </Suspense>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
