import { Routes, Route, Navigate } from "react-router-dom";
import { isAuthenticated } from "./api/client";
import Layout from "./components/Layout";
import { ErrorBoundary } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Browse from "./pages/Browse";
import Search from "./pages/Search";
import Sources from "./pages/Sources";
import Duplicates from "./pages/Duplicates";
import Analytics from "./pages/Analytics";
import SettingsIdentities from "./pages/SettingsIdentities";

function PrivateRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
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
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="browse" element={<Browse />} />
        <Route path="search" element={<Search />} />
        <Route path="sources" element={<Sources />} />
        <Route path="duplicates" element={<Duplicates />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="settings/identities" element={<SettingsIdentities />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
