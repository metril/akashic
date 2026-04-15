import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { clearToken } from "../api/client";

const navItems = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/search", label: "Search" },
  { to: "/sources", label: "Sources" },
  { to: "/duplicates", label: "Duplicates" },
  { to: "/analytics", label: "Analytics" },
];

const sidebarStyle: React.CSSProperties = {
  width: 220,
  minHeight: "100vh",
  background: "#1a1a2e",
  color: "#e0e0e0",
  display: "flex",
  flexDirection: "column",
  padding: "24px 0",
  flexShrink: 0,
};

const logoStyle: React.CSSProperties = {
  fontSize: 22,
  fontWeight: 700,
  color: "#7c83fd",
  padding: "0 24px 24px",
  letterSpacing: 1,
  borderBottom: "1px solid #2a2a4a",
  marginBottom: 16,
};

const navLinkStyle: React.CSSProperties = {
  display: "block",
  padding: "10px 24px",
  color: "#aaa",
  textDecoration: "none",
  fontSize: 15,
  borderLeft: "3px solid transparent",
  transition: "all 0.15s",
};

const navLinkActiveStyle: React.CSSProperties = {
  ...navLinkStyle,
  color: "#fff",
  background: "rgba(124,131,253,0.12)",
  borderLeftColor: "#7c83fd",
};

const logoutBtnStyle: React.CSSProperties = {
  margin: "auto 24px 0",
  padding: "8px 0",
  background: "none",
  border: "1px solid #444",
  color: "#aaa",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 14,
};

const contentStyle: React.CSSProperties = {
  flex: 1,
  background: "#f5f6fa",
  minHeight: "100vh",
  overflow: "auto",
};

export default function Layout() {
  const navigate = useNavigate();

  function handleLogout() {
    clearToken();
    navigate("/login");
  }

  return (
    <div style={{ display: "flex" }}>
      <aside style={sidebarStyle}>
        <div style={logoStyle}>Akashic</div>
        <nav>
          {navItems.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              style={({ isActive }) =>
                isActive ? navLinkActiveStyle : navLinkStyle
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <button style={logoutBtnStyle} onClick={handleLogout}>
          Logout
        </button>
      </aside>
      <main style={contentStyle}>
        <Outlet />
      </main>
    </div>
  );
}
