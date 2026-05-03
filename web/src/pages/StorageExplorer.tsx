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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { Sunburst } from "../components/storage/Sunburst";
import { ContextMenu, type ContextMenuItem } from "../components/storage/ContextMenu";
import { HoverSidebar } from "../components/storage/HoverSidebar";
import type { ColorMode, LayoutMode } from "./StorageExplorer.types";

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
  const layoutMode = (params.get("layout") as LayoutMode) ?? "treemap";

  // Hover state lifted from the canvas so HoverSidebar can read the
  // current path's breadcrumb chain. Both Treemap and Sunburst feed
  // into the same setter via onHoverChange.
  const [hoverChain, setHoverChain] = useState<TreeNode[] | null>(null);

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

  // Tree fetch — only runs in single-source mode. In cross-source
  // mode (>1 source, no `?source=` selected) we render a synthetic
  // root made of the source list so the user gets the same
  // treemap/sunburst affordances (drill-in by click, wheel-zoom-drill,
  // hover chain) at the top level instead of a flat list.
  const treeQ = useQuery<TreeResponse>({
    queryKey: ["storage", "tree", sourceId, path, colorMode],
    queryFn: () =>
      api.get<TreeResponse>(
        `/storage/tree?source_id=${sourceId}&path=${encodeURIComponent(path)}&color_by=${colorMode}&max_nodes=${DEFAULT_TREE_NODES}`,
      ),
    enabled: !!sourceId,
  });

  // v0.4.15 — synthetic root for the cross-source view. Each source
  // becomes a directory pseudo-node with a `__source:<uuid>` sentinel
  // path; click/drill handlers detect that prefix and route to
  // `enterSource()` instead of `setPath()`.
  //
  // v0.4.17:
  //   - color_key uses an index-derived key (`s0`, `s1`, …) so each
  //     source gets a distinct palette hue regardless of source_type
  //     — multiple same-type sources are no longer painted the same
  //     colour. The `s0`..`s9` keys hash to all 10 distinct PALETTE
  //     entries via `colorFor`'s string hash; collisions only start
  //     past 10 sources, which is fine.
  //   - layout_weight is sqrt(size_bytes) so a small source (e.g.
  //     Music at 100 GB next to 50 TB sources) doesn't compress to
  //     a hairline rect. size_bytes itself stays accurate, so the
  //     hover tooltip still shows the real size.
  const SOURCE_SENTINEL_PREFIX = "__source:";
  const crossSourceRoot: TreeNode | null = useMemo(() => {
    if (sourceId) return null;
    const list = sourcesQ.data?.sources;
    if (!list || list.length <= 1) return null;
    return {
      kind: "directory",
      name: "All sources",
      path: "/",
      size_bytes: 0,
      children: list.map((s, i) => ({
        kind: "directory",
        name: s.source_name,
        path: `${SOURCE_SENTINEL_PREFIX}${s.source_id}`,
        size_bytes: s.size_bytes,
        layout_weight: Math.sqrt(Math.max(s.size_bytes, 1)),
        color_key: `s${i}`,
      })),
    };
  }, [sourceId, sourcesQ.data]);

  const isSourcePseudoNode = (node: TreeNode): boolean =>
    node.path.startsWith(SOURCE_SENTINEL_PREFIX);

  const handleTreemapDirClick = (node: TreeNode) => {
    if (isSourcePseudoNode(node)) {
      enterSource(node.path.slice(SOURCE_SENTINEL_PREFIX.length));
    } else {
      setPath(node.path);
    }
  };

  const handleTreemapLeafClick = (node: TreeNode) => {
    // In cross-source view leaves don't exist (sources are directories);
    // in single-source view, leaves are file entries with an `id`.
    if (node.id) openEntry(node.id);
  };

  // Resize: the SVG treemap is purely a function of (data, w, h). A
  // callback ref drives the ResizeObserver so the observer attaches
  // exactly when the container element mounts (not "once on first
  // render" — first render typically shows a Spinner while the query
  // resolves, the container div doesn't exist yet, and `useLayoutEffect`
  // with `[]` deps would silently no-op).
  //
  // Stable identity via useCallback is mandatory: React calls a
  // callback ref(null) then ref(el) whenever the function reference
  // changes between renders. Without useCallback the observer would
  // detach + reattach on every render, and each reattach fires
  // setSize with a fresh `{w,h}` object — same numbers but different
  // identity — forcing another render in an infinite loop (React
  // error #185).
  const [size, setSize] = useState({ w: 0, h: 0 });
  const observerRef = useRef<ResizeObserver | null>(null);
  const setContainerRef = useCallback((el: HTMLDivElement | null) => {
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (!el) return;
    const update = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      // Bail out when nothing actually changed — paranoid guard in
      // case a parent layout flips back to identical dimensions.
      setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    observerRef.current = ro;
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

  const setLayoutMode = (mode: LayoutMode) => {
    const next = new URLSearchParams(params);
    next.set("layout", mode);
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
          <LayoutToggle value={layoutMode} onChange={setLayoutMode} />
          <ColorModeToggle
            value={colorMode}
            onChange={setColorMode}
            allowRisk={isAdmin}
          />
        </div>
      </Card>

      {(() => {
        // Empty cross-source state — no sources at all → kick to /sources.
        if (!sourceId && !sourcesQ.isLoading && (sourcesQ.data?.sources.length ?? 0) === 0) {
          return (
            <SourceList
              sources={[]}
              loading={false}
              onPick={enterSource}
              onEmpty={() => navigate("/sources")}
            />
          );
        }
        // Cross-source view: render the synthetic root through the
        // same treemap/sunburst widgets as a single-source tree, so
        // hover, drill-in, wheel-zoom-drill, sidebar all work the
        // same. Source pseudo-nodes' clicks route through
        // `handleTreemapDirClick` which detects the sentinel path.
        const isCrossSource = !sourceId && crossSourceRoot != null;
        const root = isCrossSource ? crossSourceRoot : treeQ.data?.root ?? null;
        const loading = isCrossSource ? sourcesQ.isLoading : treeQ.isLoading;
        const error = isCrossSource ? null : treeQ.isError ? treeQ.error : null;
        const truncated = isCrossSource ? false : treeQ.data?.truncated ?? false;

        return (
          <Card padding="none" className="overflow-hidden">
            {loading ? (
              <div className="flex justify-center items-center h-[600px] text-fg-subtle">
                <Spinner />
              </div>
            ) : error ? (
              <div className="p-6">
                <EmptyState
                  title="Couldn't load the treemap"
                  description={error instanceof Error ? error.message : "Unknown error"}
                />
              </div>
            ) : !root ? (
              <EmptyState
                title="Nothing here"
                description="This folder is empty, hidden by your access permissions, or not yet indexed."
              />
            ) : (
              <>
                {truncated && (
                  <div className="px-4 py-2 text-xs text-amber-700 bg-amber-50 dark:bg-amber-950/30 dark:text-amber-200 border-b border-amber-200/60 dark:border-amber-700/40">
                    Showing top {DEFAULT_TREE_NODES.toLocaleString()} items by
                    size. Click a directory to focus deeper detail.
                  </div>
                )}
                <div
                  className="flex w-full"
                  style={{ height: "calc(100vh - 240px)", minHeight: 480 }}
                >
                  <div ref={setContainerRef} className="relative flex-1">
                    {layoutMode === "sunburst" ? (
                      <Sunburst
                        root={root}
                        width={size.w}
                        height={size.h}
                        mode={colorMode}
                        onLeafClick={handleTreemapLeafClick}
                        onDirClick={handleTreemapDirClick}
                        onContextMenu={(node, x, y) => {
                          if (isSourcePseudoNode(node)) return;
                          setCtx({ node, x, y });
                        }}
                        onHoverChange={setHoverChain}
                        onGoUp={isCrossSource ? undefined : goUp}
                      />
                    ) : (
                      <Treemap
                        root={root}
                        width={size.w}
                        height={size.h}
                        mode={colorMode}
                        onLeafClick={handleTreemapLeafClick}
                        onDirClick={handleTreemapDirClick}
                        onContextMenu={(node, x, y) => {
                          if (isSourcePseudoNode(node)) return;
                          setCtx({ node, x, y });
                        }}
                        onHoverChange={setHoverChain}
                        onGoUp={isCrossSource ? undefined : goUp}
                      />
                    )}
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
                  <aside className="w-60 flex-shrink-0 border-l border-line-subtle bg-surface/40 overflow-y-auto">
                    <HoverSidebar
                      chain={hoverChain}
                      sourceId={sourceId}
                      onPathClick={setPath}
                    />
                  </aside>
                </div>
              </>
            )}
          </Card>
        );
      })()}

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

function LayoutToggle({
  value, onChange,
}: {
  value: LayoutMode;
  onChange: (m: LayoutMode) => void;
}) {
  const modes: { id: LayoutMode; label: string }[] = [
    { id: "treemap", label: "Treemap" },
    { id: "sunburst", label: "Sunburst" },
  ];
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
