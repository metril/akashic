import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import SearchBar from "../components/SearchBar";
import { api } from "../api/client";
import type { SearchResult, Source } from "../types";

const pageStyle: React.CSSProperties = {
  padding: "32px 40px",
};

const headingStyle: React.CSSProperties = {
  fontSize: 26,
  fontWeight: 700,
  color: "#1a1a2e",
  marginBottom: 24,
};

const filtersRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 12,
  marginTop: 12,
  marginBottom: 24,
  flexWrap: "wrap",
  alignItems: "center",
};

const selectStyle: React.CSSProperties = {
  padding: "9px 12px",
  fontSize: 14,
  border: "1.5px solid #d0d5e8",
  borderRadius: 7,
  background: "#fff",
  outline: "none",
};

const inputSmallStyle: React.CSSProperties = {
  ...selectStyle,
  width: 130,
};

const resultCardStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 8,
  padding: "14px 18px",
  marginBottom: 10,
  boxShadow: "0 1px 6px rgba(0,0,0,0.06)",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
};

const fileNameStyle: React.CSSProperties = {
  fontWeight: 600,
  fontSize: 15,
  color: "#1a1a2e",
  marginBottom: 3,
};

const fileMetaStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#999",
};

const fileSizeStyle: React.CSSProperties = {
  fontSize: 13,
  color: "#7c83fd",
  fontWeight: 600,
  whiteSpace: "nowrap",
};

const statusStyle: React.CSSProperties = {
  color: "#aaa",
  fontSize: 14,
  marginTop: 20,
  textAlign: "center",
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

export default function Search() {
  const [query, setQuery] = useState("");
  const [sourceId, setSourceId] = useState<string>("");
  const [extension, setExtension] = useState("");
  const [minSize, setMinSize] = useState("");
  const [maxSize, setMaxSize] = useState("");

  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });

  const searchQuery = useQuery<SearchResponse>({
    queryKey: ["search", query, sourceId, extension, minSize, maxSize],
    queryFn: () => {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      if (sourceId) params.set("source_id", sourceId);
      if (extension) params.set("extension", extension);
      if (minSize) params.set("min_size", minSize);
      if (maxSize) params.set("max_size", maxSize);
      return api.get<SearchResponse>(`/search?${params.toString()}`);
    },
    enabled: query.trim().length > 0,
  });

  const results = searchQuery.data?.results ?? [];

  return (
    <div style={pageStyle}>
      <div style={headingStyle}>Search Files</div>

      <SearchBar value={query} onChange={setQuery} placeholder="Search files by name, content..." />

      <div style={filtersRowStyle}>
        <select
          style={selectStyle}
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
        >
          <option value="">All Sources</option>
          {(sourcesQuery.data ?? []).map((s) => (
            <option key={s.id} value={String(s.id)}>
              {s.name}
            </option>
          ))}
        </select>
        <input
          style={inputSmallStyle}
          type="text"
          value={extension}
          onChange={(e) => setExtension(e.target.value)}
          placeholder="Extension (e.g. pdf)"
        />
        <input
          style={inputSmallStyle}
          type="number"
          value={minSize}
          onChange={(e) => setMinSize(e.target.value)}
          placeholder="Min size (bytes)"
        />
        <input
          style={inputSmallStyle}
          type="number"
          value={maxSize}
          onChange={(e) => setMaxSize(e.target.value)}
          placeholder="Max size (bytes)"
        />
      </div>

      {searchQuery.isLoading && <div style={statusStyle}>Searching...</div>}
      {searchQuery.isError && (
        <div style={{ ...statusStyle, color: "#e74c3c" }}>
          Error: {searchQuery.error instanceof Error ? searchQuery.error.message : "Search failed"}
        </div>
      )}
      {!query.trim() && (
        <div style={statusStyle}>Enter a search query to find files.</div>
      )}

      {searchQuery.data && (
        <div style={{ marginBottom: 12, color: "#666", fontSize: 13 }}>
          {searchQuery.data.total} result{searchQuery.data.total !== 1 ? "s" : ""} found
        </div>
      )}

      {results.map((file) => (
        <div key={file.id} style={resultCardStyle}>
          <div>
            <div style={fileNameStyle}>{file.filename}</div>
            <div style={fileMetaStyle}>{file.path}</div>
            {file.extension && (
              <div style={fileMetaStyle}>Type: .{file.extension}</div>
            )}
          </div>
          <div style={fileSizeStyle}>{formatBytes(file.size_bytes ?? 0)}</div>
        </div>
      ))}
    </div>
  );
}
