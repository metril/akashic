import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { StorageByType, StorageBySource, LargestFile } from "../types";

const pageStyle: React.CSSProperties = {
  padding: "32px 40px",
};

const headingStyle: React.CSSProperties = {
  fontSize: 26,
  fontWeight: 700,
  color: "#1a1a2e",
  marginBottom: 28,
};

const sectionStyle: React.CSSProperties = {
  marginBottom: 40,
};

const sectionTitleStyle: React.CSSProperties = {
  fontSize: 18,
  fontWeight: 600,
  color: "#1a1a2e",
  marginBottom: 14,
};

const tableWrapStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 10,
  overflow: "hidden",
  boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
};

const thStyle: React.CSSProperties = {
  padding: "12px 18px",
  textAlign: "left",
  fontSize: 13,
  fontWeight: 600,
  color: "#666",
  background: "#f8f9fc",
  borderBottom: "1px solid #eee",
};

const tdStyle: React.CSSProperties = {
  padding: "12px 18px",
  fontSize: 14,
  color: "#333",
  borderBottom: "1px solid #f2f2f2",
};

const barContainerStyle: React.CSSProperties = {
  background: "#f0f0f5",
  borderRadius: 4,
  height: 8,
  width: 120,
  overflow: "hidden",
};

function BarCell({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div style={barContainerStyle}>
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          background: "#7c83fd",
          borderRadius: 4,
        }}
      />
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export default function Analytics() {
  const typeQuery = useQuery<StorageByType[]>({
    queryKey: ["analytics", "storage-by-type"],
    queryFn: () => api.get<StorageByType[]>("/analytics/storage-by-type"),
  });

  const sourceQuery = useQuery<StorageBySource[]>({
    queryKey: ["analytics", "storage-by-source"],
    queryFn: () => api.get<StorageBySource[]>("/analytics/storage-by-source"),
  });

  const largestQuery = useQuery<LargestFile[]>({
    queryKey: ["analytics", "largest-files"],
    queryFn: () => api.get<LargestFile[]>("/analytics/largest-files"),
  });

  const typeData = typeQuery.data ?? [];
  const sourceData = sourceQuery.data ?? [];
  const largestData = largestQuery.data ?? [];

  const maxTypeSize = Math.max(...typeData.map((r) => r.total_size ?? 0), 1);
  const maxSourceSize = Math.max(...sourceData.map((r) => r.total_size ?? 0), 1);

  return (
    <div style={pageStyle}>
      <div style={headingStyle}>Analytics</div>

      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>Storage by File Type</div>
        {typeQuery.isLoading ? (
          <div style={{ color: "#aaa" }}>Loading...</div>
        ) : typeData.length === 0 ? (
          <div style={{ color: "#aaa", fontSize: 14 }}>No data available.</div>
        ) : (
          <div style={tableWrapStyle}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Extension</th>
                  <th style={thStyle}>Files</th>
                  <th style={thStyle}>Total Size</th>
                  <th style={thStyle}>Distribution</th>
                </tr>
              </thead>
              <tbody>
                {typeData.map((row) => (
                  <tr key={row.extension}>
                    <td style={tdStyle}>
                      <code style={{ background: "#f0f0f5", padding: "2px 7px", borderRadius: 4, fontSize: 13 }}>
                        {row.extension || "(none)"}
                      </code>
                    </td>
                    <td style={tdStyle}>{row.count.toLocaleString()}</td>
                    <td style={tdStyle}>{formatBytes(row.total_size ?? 0)}</td>
                    <td style={tdStyle}>
                      <BarCell value={row.total_size ?? 0} max={maxTypeSize} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>Storage by Source</div>
        {sourceQuery.isLoading ? (
          <div style={{ color: "#aaa" }}>Loading...</div>
        ) : sourceData.length === 0 ? (
          <div style={{ color: "#aaa", fontSize: 14 }}>No data available.</div>
        ) : (
          <div style={tableWrapStyle}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Source</th>
                  <th style={thStyle}>Files</th>
                  <th style={thStyle}>Total Size</th>
                  <th style={thStyle}>Distribution</th>
                </tr>
              </thead>
              <tbody>
                {sourceData.map((row) => (
                  <tr key={row.source_id}>
                    <td style={tdStyle}>{row.source_id}</td>
                    <td style={tdStyle}>{row.count.toLocaleString()}</td>
                    <td style={tdStyle}>{formatBytes(row.total_size ?? 0)}</td>
                    <td style={tdStyle}>
                      <BarCell value={row.total_size ?? 0} max={maxSourceSize} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>Largest Files</div>
        {largestQuery.isLoading ? (
          <div style={{ color: "#aaa" }}>Loading...</div>
        ) : largestData.length === 0 ? (
          <div style={{ color: "#aaa", fontSize: 14 }}>No data available.</div>
        ) : (
          <div style={tableWrapStyle}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Name</th>
                  <th style={thStyle}>Size</th>
                  <th style={thStyle}>Path</th>
                </tr>
              </thead>
              <tbody>
                {largestData.map((file) => (
                  <tr key={file.id}>
                    <td style={tdStyle}>{file.filename}</td>
                    <td style={{ ...tdStyle, fontWeight: 600, color: "#7c83fd" }}>
                      {formatBytes(file.size_bytes)}
                    </td>
                    <td style={{ ...tdStyle, color: "#aaa", fontSize: 12, wordBreak: "break-all" }}>
                      {file.path}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
