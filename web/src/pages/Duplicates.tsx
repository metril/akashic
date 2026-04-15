import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DuplicateGroup, FileEntry } from "../types";

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
  marginBottom: 28,
};

const groupCardStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 10,
  marginBottom: 14,
  boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
  overflow: "hidden",
};

const groupHeaderStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "16px 20px",
  cursor: "pointer",
  borderBottom: "1px solid #f0f0f0",
};

const hashStyle: React.CSSProperties = {
  fontFamily: "monospace",
  fontSize: 13,
  color: "#7c83fd",
  marginBottom: 3,
};

const metaStyle: React.CSSProperties = {
  fontSize: 13,
  color: "#888",
};

const wastedStyle: React.CSSProperties = {
  fontWeight: 700,
  color: "#e74c3c",
  fontSize: 15,
};

const fileListStyle: React.CSSProperties = {
  padding: "0 20px 12px",
};

const fileItemStyle: React.CSSProperties = {
  padding: "10px 0",
  borderBottom: "1px solid #f5f5f5",
  fontSize: 13,
  color: "#444",
};

const filePathStyle: React.CSSProperties = {
  color: "#aaa",
  fontSize: 12,
  marginTop: 2,
  wordBreak: "break-all",
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function DuplicateGroupRow({ group }: { group: DuplicateGroup }) {
  const [expanded, setExpanded] = useState(false);

  const filesQuery = useQuery<FileEntry[]>({
    queryKey: ["duplicates", group.hash, "files"],
    queryFn: () => api.get<FileEntry[]>(`/duplicates/${group.hash}/files`),
    enabled: expanded,
  });

  return (
    <div style={groupCardStyle}>
      <div style={groupHeaderStyle} onClick={() => setExpanded((v) => !v)}>
        <div>
          <div style={hashStyle}>{group.hash.substring(0, 16)}...</div>
          <div style={metaStyle}>
            {group.file_count} copies &middot; {formatBytes(group.total_size)} total
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div>
            <div style={{ fontSize: 11, color: "#bbb", textAlign: "right" }}>Wasted space</div>
            <div style={wastedStyle}>{formatBytes(group.wasted_size)}</div>
          </div>
          <span style={{ color: "#ccc", fontSize: 18 }}>{expanded ? "▲" : "▼"}</span>
        </div>
      </div>
      {expanded && (
        <div style={fileListStyle}>
          {filesQuery.isLoading && (
            <div style={{ color: "#aaa", padding: "10px 0", fontSize: 13 }}>Loading files...</div>
          )}
          {(filesQuery.data ?? []).map((file) => (
            <div key={file.id} style={fileItemStyle}>
              <div>{file.name}</div>
              <div style={filePathStyle}>{file.path}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Duplicates() {
  const { data: groups, isLoading, error } = useQuery<DuplicateGroup[]>({
    queryKey: ["duplicates"],
    queryFn: () => api.get<DuplicateGroup[]>("/duplicates"),
  });

  const sorted = [...(groups ?? [])].sort((a, b) => b.wasted_size - a.wasted_size);

  const totalWasted = sorted.reduce((s, g) => s + g.wasted_size, 0);

  return (
    <div style={pageStyle}>
      <div style={headingStyle}>Duplicate Files</div>
      <div style={subheadStyle}>
        {sorted.length} duplicate group{sorted.length !== 1 ? "s" : ""} found
        {totalWasted > 0 && ` — ${formatBytes(totalWasted)} wasted space total`}
      </div>

      {isLoading && <div style={{ color: "#aaa" }}>Scanning for duplicates...</div>}
      {error && <div style={{ color: "#e74c3c" }}>Error loading duplicates</div>}

      {sorted.length === 0 && !isLoading && (
        <div style={{ color: "#aaa", fontSize: 14 }}>No duplicate files found.</div>
      )}

      {sorted.map((group) => (
        <DuplicateGroupRow key={group.hash} group={group} />
      ))}
    </div>
  );
}
