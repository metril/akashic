import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

const pageStyle: React.CSSProperties = {
  minHeight: "100vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#f5f6fa",
};

const cardStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  padding: "40px 48px",
  boxShadow: "0 4px 32px rgba(0,0,0,0.10)",
  width: 360,
};

const titleStyle: React.CSSProperties = {
  fontSize: 28,
  fontWeight: 700,
  color: "#1a1a2e",
  marginBottom: 8,
  textAlign: "center",
};

const subtitleStyle: React.CSSProperties = {
  color: "#888",
  textAlign: "center",
  marginBottom: 28,
  fontSize: 14,
};

const labelStyle: React.CSSProperties = {
  display: "block",
  marginBottom: 6,
  fontWeight: 500,
  fontSize: 14,
  color: "#444",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  fontSize: 15,
  border: "1.5px solid #d0d5e8",
  borderRadius: 7,
  outline: "none",
  boxSizing: "border-box",
  marginBottom: 18,
};

const buttonStyle: React.CSSProperties = {
  width: "100%",
  padding: "11px 0",
  background: "#7c83fd",
  color: "#fff",
  border: "none",
  borderRadius: 7,
  fontSize: 16,
  fontWeight: 600,
  cursor: "pointer",
  marginTop: 4,
};

const errorStyle: React.CSSProperties = {
  color: "#e74c3c",
  fontSize: 13,
  marginBottom: 14,
  textAlign: "center",
};

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
    <div style={pageStyle}>
      <div style={cardStyle}>
        <div style={titleStyle}>Akashic</div>
        <div style={subtitleStyle}>Sign in to your account</div>
        <form onSubmit={handleSubmit}>
          <label style={labelStyle}>Username</label>
          <input
            style={inputStyle}
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Enter username"
            autoComplete="username"
            required
          />
          <label style={labelStyle}>Password</label>
          <input
            style={inputStyle}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter password"
            autoComplete="current-password"
            required
          />
          {error && <div style={errorStyle}>{error}</div>}
          <button style={buttonStyle} type="submit" disabled={loading}>
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}
