/**
 * Storage Explorer — WinDirStat / DaisyDisk-style nested treemap.
 *
 * Two view modes:
 *   - Cross-source (no `?source=`): one rectangle per source, sized by
 *     latest scan_snapshot. Auto-skipped when there's exactly one source.
 *   - Single-source (`?source=<id>`): the entire indexed subtree
 *     rendered as a nested squarified treemap, every file as its own
 *     coloured leaf, drill-down by clicking a directory rectangle.
 *
 * URL state owns navigation: ?source=, ?path=, ?color=. A bookmark of
 * the URL restores the exact view.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import {
  Breadcrumb,
  Card,
  EmptyState,
  Page,
  Spinner,
} from "../components/ui";
import type { BreadcrumbSegment } from "../components/ui";
import { formatBytes, formatNumber } from "../lib/format";
import { useAuth } from "../hooks/useAuth";
import { useEntryDetail } from "../hooks/useEntryDetail";
import { serialize as serializeFilters } from "../lib/filterGrammar";
import type { Predicate } from "../lib/filterGrammar";
import { Treemap, type TreeNode } from "../components/storage/Treemap";
import { ContextMenu, type ContextMenuItem } from "../components/storage/ContextMenu";
import type { ColorMode } from "./StorageExplorer.types";

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

interface TreeResponse {
  source_id: string | null;
  path: string;
  color_by: ColorMode;
  enforced: boolean;
  node_count: number;
  truncated: boolean;
  root: TreeNode | null;
}

const DEFAULT_TREE_NODES = 5000;

export default function StorageExplorer() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const { openEntry } = useEntryDetail();

  const sourceId = params.get("source") ?? "";
  const path = params.get("path") ?? "/";
  const colorMode = (params.get("color") as ColorMode) ?? "type";

  // Cross-source listing — used both for the initial route and to
  // trigger the auto-enter for single-source deployments.
  const sourcesQ = useQuery<SourcesResponse>({
    queryKey: ["storage", "sources"],
    queryFn: () => api.get<SourcesResponse>("/storage/sources"),
  });

  // Auto-enter single source: when the user has exactly one source and
  // hasn't picked one explicitly, jump straight into its tree. This is
  // the user's actual frustration — for them there's nothing to choose.
  useEffect(() => {
    if (sourceId) return;
    const list = sourcesQ.data?.sources;
    if (!list || list.length !== 1) return;
    const next = new URLSearchParams(params);
    next.set("source", list[0].source_id);
    next.set("path", "/");
    setParams(next, { replace: true });
  }, [sourceId, sourcesQ.data, params, setParams]);

  // Tree fetch — only runs in single-source mode. The cross-source
  // view falls back to /storage/sources rendered as a small list so
  // multi-source deployments still get a chooser.
  const treeQ = useQuery<TreeResponse>({
    queryKey: ["storage", "tree", sourceId, path, colorMode],
    queryFn: () =>
      api.get<TreeResponse>(
        `/storage/tree?source_id=${sourceId}&path=${encodeURIComponent(path)}&color_by=${colorMode}&max_nodes=${DEFAULT_TREE_NODES}`,
      ),
    enabled: !!sourceId,
  });

  // Resize: the SVG treemap is purely a function of (data, w, h). The
  // ResizeObserver-backed container reports its current size; we re-run
  // the d3 layout via the Treemap component on every change.
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      setSize({ w: el.clientWidth, h: el.clientHeight });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

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
    if (!sourceId) return;
    if (path === "/") {
      // At source root, "up" means back to the cross-source list. We
      // only allow this when there's more than one source — otherwise
      // we'd just bounce right back via auto-enter.
      const list = sourcesQ.data?.sources ?? [];
      if (list.length > 1) exitToSources();
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

  // Context-menu state — driven by Treemap's right-click callback.
  const [ctx, setCtx] = useState<{
    node: TreeNode;
    x: number;
    y: number;
  } | null>(null);

  const ctxItems: ContextMenuItem[] = useMemo(() => {
    if (!ctx || !sourceId) return [];
    const node = ctx.node;
    const items: ContextMenuItem[] = [
      {
        label: "Open in Browse",
        onClick: () => {
          const browsePath =
            node.kind === "directory"
              ? node.path
              : node.path.split("/").slice(0, -1).join("/") || "/";
          navigate(`/browse?source=${sourceId}&path=${encodeURIComponent(browsePath)}`);
        },
      },
      {
        label: "Filter Search to this folder",
        onClick: () => {
          const target =
            node.kind === "directory"
              ? node.path
              : node.path.split("/").slice(0, -1).join("/") || "/";
          const pred: Predicate = { kind: "path", value: target };
          navigate(`/search?filters=${serializeFilters([pred])}`);
        },
      },
    ];
    return items;
  }, [ctx, sourceId, navigate]);

  // Breadcrumbs follow the URL path, with "All sources" as the leftmost
  // segment when more than one source exists. Single-source deployments
  // hide it because there's nowhere meaningful to go back to.
  const breadcrumbs: BreadcrumbSegment[] = useMemo(() => {
    const list = sourcesQ.data?.sources ?? [];
    const segs: BreadcrumbSegment[] = [];
    if (list.length > 1) {
      segs.push({ label: "All sources", onClick: exitToSources });
    }
    if (sourceId) {
      const sourceName =
        list.find((s) => s.source_id === sourceId)?.source_name ??
        sourceId.slice(0, 8);
      segs.push({ label: sourceName, onClick: () => setPath("/") });
      let acc = "";
      for (const part of path.split("/").filter(Boolean)) {
        acc = `${acc}/${part}`;
        const target = acc;
        segs.push({ label: part, onClick: () => setPath(target) });
      }
    }
    return segs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourcesQ.data, sourceId, path]);

  return (
    <Page
      title="Storage"
      description="Treemap of indexed storage. Every file is its own rectangle."
      width="full"
    >
      <Card padding="sm" className="mb-4">
        <div className="flex flex-wrap items-center gap-3 px-2 py-1">
          <button
            type="button"
            onClick={goUp}
            disabled={!sourceId || (path === "/" && (sourcesQ.data?.sources.length ?? 0) <= 1)}
            className="text-xs text-fg-muted hover:text-fg disabled:opacity-40 disabled:cursor-not-allowed"
            title="Up one level"
          >
            ⬆ Up
          </button>
          {breadcrumbs.length > 0 && <Breadcrumb segments={breadcrumbs} />}
          <div className="flex-1" />
          <ColorModeToggle
            value={colorMode}
            onChange={setColorMode}
            allowRisk={isAdmin}
          />
        </div>
      </Card>

      {!sourceId ? (
        <SourceList
          sources={sourcesQ.data?.sources ?? []}
          loading={sourcesQ.isLoading}
          onPick={enterSource}
          onEmpty={() => navigate("/sources")}
        />
      ) : (
        <Card padding="none" className="overflow-hidden">
          {treeQ.isLoading ? (
            <div className="flex justify-center items-center h-[600px] text-fg-subtle">
              <Spinner />
            </div>
          ) : treeQ.isError ? (
            <div className="p-6">
              <EmptyState
                title="Couldn't load the treemap"
                description={
                  treeQ.error instanceof Error
                    ? treeQ.error.message
                    : "Unknown error"
                }
              />
            </div>
          ) : !treeQ.data?.root ? (
            <EmptyState
              title="Nothing here"
              description="This folder is empty, hidden by your access permissions, or not yet indexed."
            />
          ) : (
            <>
              {treeQ.data.truncated && (
                <div className="px-4 py-2 text-xs text-amber-700 bg-amber-50 dark:bg-amber-950/30 dark:text-amber-200 border-b border-amber-200/60 dark:border-amber-700/40">
                  Showing top {DEFAULT_TREE_NODES.toLocaleString()} items by
                  size. Click a directory to focus deeper detail.
                </div>
              )}
              <div
                ref={containerRef}
                className="relative w-full"
                style={{ height: "calc(100vh - 240px)", minHeight: 480 }}
              >
                <Treemap
                  root={treeQ.data.root}
                  width={size.w}
                  height={size.h}
                  mode={colorMode}
                  onLeafClick={(node) => {
                    if (node.id) openEntry(node.id);
                  }}
                  onDirClick={(node) => setPath(node.path)}
                  onContextMenu={(node, x, y) => setCtx({ node, x, y })}
                />
                {ctx && (
                  <ContextMenu
                    x={ctx.x}
                    y={ctx.y}
                    items={ctxItems}
                    onClose={() => setCtx(null)}
                    containerWidth={size.w}
                    containerHeight={size.h}
                  />
                )}
              </div>
            </>
          )}
        </Card>
      )}

      {sourceId && treeQ.data && (
        <div className="mt-3 text-xs text-fg-muted text-right tabular-nums">
          {formatNumber(treeQ.data.node_count)} nodes
          {treeQ.data.enforced && (
            <span className="ml-2 text-accent-700">
              (filtered by your access permissions)
            </span>
          )}
        </div>
      )}
    </Page>
  );
}

// ── Cross-source list (used when multiple sources exist) ───────────────────


interface SourceListProps {
  sources: SourcesResponse["sources"];
  loading: boolean;
  onPick: (id: string) => void;
  onEmpty: () => void;
}

function SourceList({ sources, loading, onPick, onEmpty }: SourceListProps) {
  if (loading) {
    return (
      <Card padding="none">
        <div className="flex justify-center items-center h-40 text-fg-subtle">
          <Spinner />
        </div>
      </Card>
    );
  }
  if (sources.length === 0) {
    return (
      <Card padding="lg">
        <EmptyState
          title="No sources yet"
          description="Add a source on the Sources page and run a scan to see the treemap."
          action={
            <button
              type="button"
              onClick={onEmpty}
              className="text-accent-700 hover:text-accent-800 text-sm font-medium"
            >
              Open Sources →
            </button>
          }
        />
      </Card>
    );
  }
  return (
    <Card padding="none">
      <ul className="divide-y divide-line-subtle">
        {sources.map((s) => (
          <li key={s.source_id}>
            <button
              type="button"
              onClick={() => onPick(s.source_id)}
              className="w-full flex items-baseline justify-between px-4 py-3 hover:bg-surface-muted/60 transition-colors text-left"
            >
              <div className="min-w-0 flex-1">
                <div className="font-medium text-fg truncate">{s.source_name}</div>
                <div className="text-xs text-fg-muted mt-0.5">
                  {s.source_type} ·{" "}
                  {formatNumber(s.file_count)} files,{" "}
                  {formatNumber(s.directory_count)} folders
                </div>
              </div>
              <div className="text-sm font-medium text-fg tabular-nums">
                {formatBytes(s.size_bytes)}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
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
