import { useState } from "react";
import { useSources, useCreateSource, useDeleteSource } from "../hooks/useSources";

const pageStyle: React.CSSProperties = {
  padding: "32px 40px",
};

const headingStyle: React.CSSProperties = {
  fontSize: 26,
  fontWeight: 700,
  color: "#1a1a2e",
  marginBottom: 24,
};

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
  gap: 18,
  marginBottom: 36,
};

const cardStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 10,
  padding: "20px 22px",
  boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
};

const cardNameStyle: React.CSSProperties = {
  fontSize: 17,
  fontWeight: 700,
  color: "#1a1a2e",
  marginBottom: 4,
};

const cardPathStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#aaa",
  marginBottom: 10,
  wordBreak: "break-all",
};

const badgeStyle = (active: boolean): React.CSSProperties => ({
  display: "inline-block",
  padding: "2px 10px",
  borderRadius: 12,
  fontSize: 12,
  fontWeight: 600,
  background: active ? "#e8f5e9" : "#fce4ec",
  color: active ? "#2e7d32" : "#c62828",
  marginBottom: 10,
});

const cardActionsStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  marginTop: 10,
};

const btnStyle = (variant: "danger" | "default"): React.CSSProperties => ({
  padding: "6px 14px",
  fontSize: 13,
  border: "none",
  borderRadius: 6,
  cursor: "pointer",
  fontWeight: 500,
  background: variant === "danger" ? "#fce4ec" : "#e8eaf6",
  color: variant === "danger" ? "#c62828" : "#3949ab",
});

const formBoxStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 10,
  padding: "24px 28px",
  boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
  maxWidth: 480,
};

const formTitleStyle: React.CSSProperties = {
  fontSize: 18,
  fontWeight: 600,
  color: "#1a1a2e",
  marginBottom: 18,
};

const labelStyle: React.CSSProperties = {
  display: "block",
  marginBottom: 5,
  fontWeight: 500,
  fontSize: 13,
  color: "#555",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "9px 11px",
  fontSize: 14,
  border: "1.5px solid #d0d5e8",
  borderRadius: 7,
  outline: "none",
  boxSizing: "border-box",
  marginBottom: 14,
};

const submitBtnStyle: React.CSSProperties = {
  padding: "10px 22px",
  background: "#7c83fd",
  color: "#fff",
  border: "none",
  borderRadius: 7,
  fontSize: 15,
  fontWeight: 600,
  cursor: "pointer",
};

const lastScanStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#bbb",
};

export default function Sources() {
  const { data: sources, isLoading, error } = useSources();
  const createSource = useCreateSource();
  const deleteSource = useDeleteSource();

  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [sourceType, setSourceType] = useState("local");
  const [formError, setFormError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    try {
      await createSource.mutateAsync({
        name,
        type: sourceType,
        connection_config: { path },
      });
      setName("");
      setPath("");
      setSourceType("local");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create source");
    }
  }

  async function handleDelete(id: string) {
    if (confirm("Delete this source?")) {
      await deleteSource.mutateAsync(id);
    }
  }

  return (
    <div style={pageStyle}>
      <div style={headingStyle}>Sources</div>

      {isLoading && <div style={{ color: "#aaa" }}>Loading sources...</div>}
      {error && <div style={{ color: "#e74c3c" }}>Error loading sources</div>}

      <div style={gridStyle}>
        {(sources ?? []).map((source) => (
          <div key={source.id} style={cardStyle}>
            <div style={cardNameStyle}>{source.name}</div>
            <div style={cardPathStyle}>
              {typeof source.connection_config?.path === "string"
                ? source.connection_config.path
                : JSON.stringify(source.connection_config)}
            </div>
            <span style={badgeStyle(source.status === "active")}>
              {source.status === "active" ? "Active" : source.status}
            </span>
            <div style={lastScanStyle}>
              Type: {source.type}
              {source.last_scan_at && (
                <> &middot; Last scan: {new Date(source.last_scan_at).toLocaleDateString()}</>
              )}
            </div>
            <div style={cardActionsStyle}>
              <button
                style={btnStyle("danger")}
                onClick={() => handleDelete(source.id)}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      <div style={formBoxStyle}>
        <div style={formTitleStyle}>Add New Source</div>
        <form onSubmit={handleSubmit}>
          <label style={labelStyle}>Name</label>
          <input
            style={inputStyle}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Documents"
            required
          />
          <label style={labelStyle}>Path</label>
          <input
            style={inputStyle}
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/home/user/documents"
            required
          />
          <label style={labelStyle}>Type</label>
          <select
            style={{ ...inputStyle, marginBottom: 18 }}
            value={sourceType}
            onChange={(e) => setSourceType(e.target.value)}
          >
            <option value="local">Local</option>
            <option value="network">Network</option>
            <option value="cloud">Cloud</option>
          </select>
          {formError && (
            <div style={{ color: "#e74c3c", fontSize: 13, marginBottom: 10 }}>{formError}</div>
          )}
          <button style={submitBtnStyle} type="submit" disabled={createSource.isPending}>
            {createSource.isPending ? "Adding..." : "Add Source"}
          </button>
        </form>
      </div>
    </div>
  );
}
