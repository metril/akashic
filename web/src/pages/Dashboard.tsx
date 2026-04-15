import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Source, StorageByType } from "../types";

const pageStyle: React.CSSProperties = {
  padding: "32px 40px",
};

const headingStyle: React.CSSProperties = {
  fontSize: 26,
  fontWeight: 700,
  color: "#1a1a2e",
  marginBottom: 8,
};

const subheadStyle: React.CSSProperties = {
  color: "#888",
  fontSize: 14,
  marginBottom: 32,
};

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
  gap: 20,
  marginBottom: 36,
};

const cardStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 10,
  padding: "22px 26px",
  boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
};

const cardValueStyle: React.CSSProperties = {
  fontSize: 32,
  fontWeight: 700,
  color: "#7c83fd",
  marginBottom: 4,
};

const cardLabelStyle: React.CSSProperties = {
  fontSize: 13,
  color: "#888",
  fontWeight: 500,
};

const sectionTitleStyle: React.CSSProperties = {
  fontSize: 18,
  fontWeight: 600,
  color: "#1a1a2e",
  marginBottom: 16,
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  background: "#fff",
  borderRadius: 10,
  overflow: "hidden",
  boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
};

const thStyle: React.CSSProperties = {
  padding: "12px 16px",
  textAlign: "left",
  fontSize: 13,
  fontWeight: 600,
  color: "#666",
  background: "#f8f9fc",
  borderBottom: "1px solid #eee",
};

const tdStyle: React.CSSProperties = {
  padding: "12px 16px",
  fontSize: 14,
  color: "#333",
  borderBottom: "1px solid #f0f0f0",
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export default function Dashboard() {
  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });

  const storageQuery = useQuery<StorageByType[]>({
    queryKey: ["analytics", "storage-by-type"],
    queryFn: () => api.get<StorageByType[]>("/analytics/storage-by-type"),
  });

  const sources = sourcesQuery.data ?? [];
  const storageByType = storageQuery.data ?? [];

  const totalFiles = storageByType.reduce((s, r) => s + r.file_count, 0);
  const totalSize = storageByType.reduce((s, r) => s + r.total_size, 0);
  const enabledSources = sources.filter((s) => s.enabled).length;

  return (
    <div style={pageStyle}>
      <div style={headingStyle}>Dashboard</div>
      <div style={subheadStyle}>Overview of your file archive</div>

      <div style={gridStyle}>
        <div style={cardStyle}>
          <div style={cardValueStyle}>{sources.length}</div>
          <div style={cardLabelStyle}>Total Sources</div>
        </div>
        <div style={cardStyle}>
          <div style={cardValueStyle}>{enabledSources}</div>
          <div style={cardLabelStyle}>Active Sources</div>
        </div>
        <div style={cardStyle}>
          <div style={cardValueStyle}>{totalFiles.toLocaleString()}</div>
          <div style={cardLabelStyle}>Total Files</div>
        </div>
        <div style={cardStyle}>
          <div style={cardValueStyle}>{formatBytes(totalSize)}</div>
          <div style={cardLabelStyle}>Total Storage Used</div>
        </div>
      </div>

      <div style={sectionTitleStyle}>Storage by File Type</div>
      {storageQuery.isLoading ? (
        <div style={{ color: "#888" }}>Loading...</div>
      ) : storageByType.length === 0 ? (
        <div style={{ color: "#aaa", fontSize: 14 }}>No data available.</div>
      ) : (
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thStyle}>Extension</th>
              <th style={thStyle}>File Count</th>
              <th style={thStyle}>Total Size</th>
            </tr>
          </thead>
          <tbody>
            {storageByType.slice(0, 10).map((row) => (
              <tr key={row.extension}>
                <td style={tdStyle}>{row.extension || "(none)"}</td>
                <td style={tdStyle}>{row.file_count.toLocaleString()}</td>
                <td style={tdStyle}>{formatBytes(row.total_size)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
