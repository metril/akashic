/**
 * Storage Explorer — WinDirStat / WizTree-style treemap of the indexed
 * estate. Two questions this page answers that none of the upstream
 * inspirations can:
 *
 *   1. "Where is space going across all of my sources at once?" The
 *      default view is the cross-source aggregate; clicking a source
 *      drills into it.
 *   2. "What's at risk?" — the risk color mode flags Anyone-readable
 *      and stale-SID-owned files in red/amber. Admin-only because the
 *      ACL projection it consumes is sensitive.
 *
 * URL state: ?source=&path=&color= so a treemap view is shareable.
 * The path stack is the URL — going up is a back-button or breadcrumb
 * click; going down is a tile click.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  ResponsiveContainer,
  Treemap,
  Tooltip,
} from "recharts";

import { api } from "../api/client";
import {
  Card,
  EmptyState,
  Page,
  Spinner,
  Breadcrumb,
} from "../components/ui";
import type { BreadcrumbSegment } from "../components/ui";
import { formatBytes, formatNumber } from "../lib/format";
import { useAuth } from "../hooks/useAuth";

type ColorMode = "type" | "age" | "owner" | "risk";

interface SourcesResponse {
  sources: {
    source_id: string;
    source_name: string;
    source_type: string;
    size_bytes: number;
    file_count: number;
    directory_count: number;
    taken_at: string | null;
  }[];
}

interface ChildrenResponse {
  source_id: string;
  path: string;
  color_by: ColorMode;
  children: {
    id: string;
    kind: "file" | "directory";
    name: string;
    path: string;
    size_bytes: number;
    file_count: number;
    color_key: string;
  }[];
  other: {
    kind: "other";
    name: string;
    size_bytes: number;
    child_count: number;
  } | null;
  hidden: {
    kind: "hidden";
    name: string;
    size_bytes: number;
    child_count: number;
  } | null;
  enforced: boolean;
}

// Stable categorical palette — assigning the SAME colour to the same
// color_key across drill-downs is the visual aid that makes type/owner
// modes legible. Keyed on the string itself so an unknown new value
// gets a deterministic bucket without runtime config.
const PALETTE = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#0ea5e9",
];

function colorFor(key: string, mode: ColorMode): string {
  if (mode === "age") {
    if (key === "hot") return "#10b981";
    if (key === "warm") return "#f59e0b";
    if (key === "cold") return "#94a3b8";
    return "#cbd5e1";
  }
  if (mode === "risk") {
    if (key === "public") return "#ef4444";
    if (key === "authenticated") return "#f59e0b";
    if (key === "restricted") return "#10b981";
    return "#94a3b8";
  }
  // type / owner / fallback — hash the string into the palette.
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

export default function StorageExplorer() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();

  const sourceId = params.get("source") ?? "";
  const path = params.get("path") ?? "/";
  const colorMode = (params.get("color") as ColorMode) ?? "type";

  // Cross-source mode (no source selected) — show one rectangle per
  // source. Default landing.
  const sourcesQ = useQuery<SourcesResponse>({
    queryKey: ["storage", "sources"],
    queryFn: () => api.get<SourcesResponse>("/storage/sources"),
    enabled: !sourceId,
  });

  const childrenQ = useQuery<ChildrenResponse>({
    queryKey: ["storage", "children", sourceId, path, colorMode],
    queryFn: () =>
      api.get<ChildrenResponse>(
        `/storage/children?source_id=${sourceId}&path=${encodeURIComponent(path)}&color_by=${colorMode}`,
      ),
    enabled: !!sourceId,
  });

  const setPath = (newPath: string) => {
    const next = new URLSearchParams(params);
    next.set("path", newPath);
    setParams(next);
  };

  const enterSource = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("source", id);
    next.set("path", "/");
    setParams(next);
  };

  const exitToSources = () => {
    const next = new URLSearchParams(params);
    next.delete("source");
    next.delete("path");
    setParams(next);
  };

  const goUp = () => {
    if (path === "/") {
      exitToSources();
      return;
    }
    const idx = path.lastIndexOf("/");
    setPath(idx <= 0 ? "/" : path.slice(0, idx));
  };

  const setColorMode = (mode: ColorMode) => {
    const next = new URLSearchParams(params);
    next.set("color", mode);
    setParams(next);
  };

  // Treemap rect data — the recharts shape is `{name, size, ...}` per
  // tile. We keep the original record alongside via a custom field so
  // the click handler can resolve back to a path/source.
  const treemapData = useMemo(() => {
    if (!sourceId) {
      return (sourcesQ.data?.sources ?? []).map((s) => ({
        name: s.source_name,
        size: s.size_bytes,
        // recharts uses `name`+`size`; everything else is opaque
        // payload that comes back via the tooltip / click event.
        payload_kind: "source" as const,
        payload_id: s.source_id,
        color: colorFor(s.source_type, colorMode),
        meta: `${formatNumber(s.file_count)} files`,
      }));
    }
    const data = childrenQ.data;
    if (!data) return [];
    const items: Array<{
      name: string;
      size: number;
      payload_kind: "directory" | "file" | "other" | "hidden";
      payload_path?: string;
      color: string;
      meta: string;
    }> = data.children.map((c) => ({
      name: c.name,
      size: Math.max(1, c.size_bytes),  // recharts ignores size 0 — bump to 1
      payload_kind: c.kind,
      payload_path: c.path,
      color: colorFor(c.color_key, colorMode),
      meta:
        c.kind === "directory"
          ? `${formatNumber(c.file_count)} files inside`
          : c.color_key,
    }));
    if (data.other) {
      items.push({
        name: data.other.name,
        size: Math.max(1, data.other.size_bytes),
        payload_kind: "other",
        color: "#94a3b8",
        meta: `${formatNumber(data.other.child_count)} smaller items`,
      });
    }
    if (data.hidden) {
      items.push({
        name: data.hidden.name,
        size: Math.max(1, data.hidden.size_bytes),
        payload_kind: "hidden",
        color: "#1f2937",
        meta: `${formatNumber(data.hidden.child_count)} entries you can't see`,
      });
    }
    return items;
  }, [sourceId, sourcesQ.data, childrenQ.data, colorMode]);

  const totalBytes = treemapData.reduce((s, d) => s + d.size, 0);
  const breadcrumbs: BreadcrumbSegment[] = [
    { label: "All sources", onClick: exitToSources },
  ];
  if (sourceId) {
    const sourceName =
      sourcesQ.data?.sources.find((s) => s.source_id === sourceId)?.source_name ??
      childrenQ.data?.source_id?.slice(0, 8) ??
      "Source";
    breadcrumbs.push({ label: sourceName, onClick: () => setPath("/") });
    let acc = "";
    for (const part of path.split("/").filter(Boolean)) {
      acc = `${acc}/${part}`;
      const target = acc;
      breadcrumbs.push({ label: part, onClick: () => setPath(target) });
    }
  }

  const handleTileClick = (entry: { payload_kind?: string; payload_id?: string; payload_path?: string }) => {
    if (entry.payload_kind === "source" && entry.payload_id) {
      enterSource(entry.payload_id);
    } else if (entry.payload_kind === "directory" && entry.payload_path) {
      setPath(entry.payload_path);
    }
    // file / other / hidden tiles aren't drillable — no-op.
  };

  const loading = sourceId ? childrenQ.isLoading : sourcesQ.isLoading;
  const empty =
    !loading &&
    treemapData.length === 0 &&
    !sourcesQ.isError &&
    !childrenQ.isError;

  return (
    <Page
      title="Storage"
      description="Treemap of indexed storage. Click a tile to drill in."
      width="full"
    >
      <Card padding="sm" className="mb-4">
        <div className="flex flex-wrap items-center gap-3 px-2 py-1">
          <button
            type="button"
            onClick={goUp}
            disabled={!sourceId}
            className="text-xs text-fg-muted hover:text-fg disabled:opacity-40 disabled:cursor-not-allowed"
            title="Up one level"
          >
            ⬆ Up
          </button>
          <Breadcrumb segments={breadcrumbs} />
          <div className="flex-1" />
          <ColorModeToggle
            value={colorMode}
            onChange={setColorMode}
            allowRisk={isAdmin}
          />
        </div>
      </Card>

      <Card padding="none" className="overflow-hidden">
        {loading ? (
          <div className="flex justify-center items-center h-[600px] text-fg-subtle">
            <Spinner />
          </div>
        ) : empty ? (
          <EmptyState
            title="No data to plot"
            description={
              sourceId
                ? "This folder is empty or hasn't been indexed yet."
                : "Add a source and run a scan to see the treemap."
            }
            action={
              <button
                type="button"
                onClick={() => navigate("/sources")}
                className="text-accent-700 hover:text-accent-800 text-sm font-medium"
              >
                Open Sources →
              </button>
            }
          />
        ) : (
          <div className="h-[600px]">
            <ResponsiveContainer width="100%" height="100%">
              <Treemap
                data={treemapData}
                dataKey="size"
                nameKey="name"
                isAnimationActive={false}
                content={<Tile onClick={handleTileClick} />}
              >
                <Tooltip
                  formatter={(_v, _n, item) => {
                    const p = (item?.payload ?? {}) as {
                      meta?: string;
                      size?: number;
                    };
                    return [
                      `${formatBytes(p.size ?? 0)} · ${p.meta ?? ""}`,
                      String(item?.payload?.name ?? ""),
                    ];
                  }}
                  labelFormatter={() => ""}
                />
              </Treemap>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      {treemapData.length > 0 && (
        <div className="mt-3 text-xs text-fg-muted text-right tabular-nums">
          Total: {formatBytes(totalBytes)} ·{" "}
          {treemapData.length.toLocaleString()} tile
          {treemapData.length !== 1 && "s"}
          {childrenQ.data?.enforced && (
            <span className="ml-2 text-accent-700">
              (filtered by your access permissions)
            </span>
          )}
        </div>
      )}
    </Page>
  );
}

// The custom tile content is what makes the recharts treemap usable.
// The default content wants a static name; we want a click handler,
// our own colour, and a label that truncates at narrow widths.
interface TileProps {
  // recharts injects these.
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  name?: string;
  payload?: {
    color?: string;
    payload_kind?: string;
    payload_id?: string;
    payload_path?: string;
  };
  onClick?: (entry: TileProps["payload"] & object) => void;
}

function Tile(props: TileProps) {
  const { x = 0, y = 0, width = 0, height = 0, name, payload, onClick } = props;
  const fill = payload?.color ?? "#94a3b8";
  // Don't even paint the label if there's no room — pure rect is more
  // honest than a clipped-mid-letter label.
  const labelFits = width > 60 && height > 22;
  const isClickable =
    payload?.payload_kind === "source" || payload?.payload_kind === "directory";

  return (
    <g
      onClick={() => isClickable && onClick && payload && onClick(payload)}
      style={{ cursor: isClickable ? "pointer" : "default" }}
    >
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        fill={fill}
        stroke="rgba(255,255,255,0.4)"
        strokeWidth={1}
      />
      {labelFits && (
        <text
          x={x + 6}
          y={y + 16}
          fill="white"
          fontSize={11}
          fontWeight={500}
          style={{
            pointerEvents: "none",
            textShadow: "0 1px 2px rgba(0,0,0,0.5)",
          }}
        >
          {truncate(name ?? "", Math.floor(width / 7))}
        </text>
      )}
    </g>
  );
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, Math.max(1, max - 1)) + "…";
}

function ColorModeToggle({
  value, onChange, allowRisk,
}: {
  value: ColorMode;
  onChange: (m: ColorMode) => void;
  allowRisk: boolean;
}) {
  const modes: { id: ColorMode; label: string }[] = [
    { id: "type", label: "Type" },
    { id: "age", label: "Age" },
    { id: "owner", label: "Owner" },
  ];
  if (allowRisk) modes.push({ id: "risk", label: "Risk" });
  return (
    <div role="radiogroup" className="inline-flex rounded-lg border border-line p-0.5 bg-surface text-sm">
      {modes.map((m) => (
        <button
          key={m.id}
          type="button"
          role="radio"
          aria-checked={value === m.id}
          onClick={() => onChange(m.id)}
          className={
            "px-3 py-1 rounded-md transition-colors " +
            (value === m.id
              ? "bg-accent-100 text-accent-800 dark:bg-accent-500/20 dark:text-accent-200 font-medium"
              : "text-fg-muted hover:text-fg")
          }
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
