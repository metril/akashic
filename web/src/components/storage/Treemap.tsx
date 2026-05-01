/**
 * Squarified treemap of a single nested tree, the WinDirStat /
 * DaisyDisk shape. Renders every leaf as its own coloured rectangle
 * inside its directory's container — one canvas, one zoom level, no
 * mandatory drilling.
 *
 * Rendering pipeline:
 *   1. d3.hierarchy() folds the nested input into a hierarchy of nodes.
 *   2. .sum() weights each leaf by size_bytes; interior nodes inherit.
 *   3. d3.treemap() runs squarify and lays out x0/y0/x1/y1 per node.
 *   4. We paint root.descendants() as plain SVG <rect>s, leaves coloured
 *      from the API's `color_key`, directories outlined with a label
 *      band on top.
 *
 * Visual treatment of directories (DaisyDisk-style polish):
 *   - Depth-1 ancestor's name selects an accent from the shared palette
 *     so all descendants of e.g. "Gintama" share a colour identity.
 *   - Each directory rectangle gets a header band rect (filled with the
 *     branch accent darkened by depth) + label text — a "folder tab"
 *     visual cue, not just text-on-backplate.
 *   - Backplate, border colour, and stroke width step with depth so
 *     shallow dirs feel structural, deep dirs feel atomic.
 *   - Hovering any rectangle lifts the stroke on every ancestor back to
 *     the root so the eye can trace the path.
 *
 * Click handlers route through props so the page owns navigation:
 *   - onLeafClick(leaf)   → page opens the entry-detail drawer
 *   - onDirClick(dir)     → page navigates to ?path=dir.path
 *   - onContextMenu(node, x, y) → page opens its context menu
 *   - onHoverChange(node) → page updates the HoverSidebar
 */
import { useCallback, useMemo, useRef, useState } from "react";
import {
  hierarchy as d3Hierarchy,
  treemap as d3Treemap,
  treemapSquarify,
  type HierarchyRectangularNode,
} from "d3-hierarchy";

import type { ColorMode } from "../../pages/StorageExplorer.types";
import { branchAccent, mix } from "./branchAccent";

export interface TreeNode {
  // The shape returned by /api/storage/tree. Optional id for non-leaf
  // synthetic nodes (root, <other>, <hidden>). path is unique within
  // the source for entry rows; synthetic nodes use sentinel paths
  // (`/path/to/dir/<other>`).
  id?: string;
  kind: "file" | "directory" | "other" | "hidden";
  name: string;
  path: string;
  size_bytes: number;
  color_key?: string;
  children?: TreeNode[];
}

interface TreemapProps {
  root: TreeNode;
  width: number;
  height: number;
  mode: ColorMode;
  onLeafClick?: (node: TreeNode) => void;
  onDirClick?: (node: TreeNode) => void;
  onContextMenu?: (node: TreeNode, x: number, y: number) => void;
  onHoverChange?: (chain: TreeNode[] | null) => void;
}

const PALETTE = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#0ea5e9",
];

function colorFor(key: string | undefined, mode: ColorMode): string {
  if (!key) return "#94a3b8";
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
  if (key === "other") return "#94a3b8";
  if (key === "directory") return "#475569";
  // type / owner / fallback — hash the string into the palette.
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

// Headroom on each directory rectangle for its title strip. Recursive
// because the nested layout needs each level's headroom subtracted from
// its children's available height. We collapse the strip when the rect
// is too short to host it.
function paddingTopFor(d: HierarchyRectangularNode<TreeNode>): number {
  if (d.depth === 0) return 0;
  const h = (d.y1 ?? 0) - (d.y0 ?? 0);
  return h >= 28 ? 14 : 0;
}

/**
 * Depth-aware directory chrome. Branch accent feeds the header band
 * fill (darkened toward slate as you nest deeper); plate, border, and
 * stroke width use a slate-only progression so the branch-accent
 * shows through clearly on shallow dirs and gracefully fades on
 * deeper ones.
 */
function dirStyle(depth: number, accent: string) {
  const d = Math.max(1, depth);
  const headerMix = Math.min(0.85, 0.10 + 0.10 * (d - 1));
  const plateAlpha = Math.min(0.10, 0.04 + 0.015 * (d - 1));
  const borderAlpha = Math.max(0.10, 0.32 - 0.05 * (d - 1));
  const strokeWidth = Math.max(0.5, 2.5 - 0.5 * (d - 1));
  return {
    headerFill: mix(accent, "#0f172a", headerMix),
    plateFill: `rgba(15, 23, 42, ${plateAlpha})`,
    borderFill: `rgba(15, 23, 42, ${borderAlpha})`,
    strokeWidth,
  };
}

/** Walk up to the depth-1 ancestor; its name keys the branch accent. */
function topLevelName(n: HierarchyRectangularNode<TreeNode>): string {
  let cur: HierarchyRectangularNode<TreeNode> | null = n;
  while (cur && cur.depth > 1 && cur.parent) cur = cur.parent;
  return cur?.data.name ?? "/";
}

export function Treemap({
  root,
  width,
  height,
  mode,
  onLeafClick,
  onDirClick,
  onContextMenu,
  onHoverChange,
}: TreemapProps) {
  // Layout is pure given (root, width, height) — useMemo is the right
  // shape so resize / colour-toggle / data-change all re-run cheaply.
  const layout = useMemo(() => {
    if (width <= 0 || height <= 0) return null;
    const h = d3Hierarchy<TreeNode>(root, (d) => d.children)
      .sum((d) => (d.children && d.children.length > 0 ? 0 : d.size_bytes))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    d3Treemap<TreeNode>()
      .tile(treemapSquarify)
      .size([width, height])
      .paddingInner(1)
      .paddingTop(paddingTopFor)
      .round(true)(h);
    return h as HierarchyRectangularNode<TreeNode>;
  }, [root, width, height]);

  // Hover tooltip state. Local to keep the page free of mouse events.
  const [hover, setHover] = useState<{
    node: HierarchyRectangularNode<TreeNode>;
    x: number;
    y: number;
  } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // The set of paths that are ancestors of the hovered node. Used at
  // render time to lift each ancestor's stroke + alpha so the eye can
  // trace from the hovered tile back to the root.
  const ancestorPaths = useMemo(() => {
    if (!hover) return new Set<string>();
    const set = new Set<string>();
    let cur: HierarchyRectangularNode<TreeNode> | null = hover.node;
    while (cur) { set.add(cur.data.path); cur = cur.parent ?? null; }
    return set;
  }, [hover]);

  const setHoverNode = useCallback(
    (n: HierarchyRectangularNode<TreeNode> | null, x: number, y: number) => {
      if (!n) {
        setHover(null);
        onHoverChange?.(null);
        return;
      }
      setHover({ node: n, x, y });
      // Build the chain top-down so the sidebar reads root → leaf.
      const chain: TreeNode[] = [];
      let cur: HierarchyRectangularNode<TreeNode> | null = n;
      while (cur) { chain.unshift(cur.data); cur = cur.parent ?? null; }
      onHoverChange?.(chain);
    },
    [onHoverChange],
  );

  if (!layout || width <= 0 || height <= 0) {
    return <div ref={containerRef} className="w-full h-full" />;
  }

  const nodes = layout.descendants();

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full"
      onMouseLeave={() => setHoverNode(null, 0, 0)}
    >
      <svg
        width={width}
        height={height}
        // Stops the browser from selecting text on rapid double-clicks.
        style={{ userSelect: "none" }}
      >
        {nodes.map((n) => {
          const x0 = n.x0 ?? 0;
          const y0 = n.y0 ?? 0;
          const x1 = n.x1 ?? 0;
          const y1 = n.y1 ?? 0;
          const w = x1 - x0;
          const h = y1 - y0;
          if (w < 1 || h < 1) return null;

          const isRoot = n.depth === 0;
          const data = n.data;
          const isDir = data.kind === "directory" || data.kind === "hidden";
          const isLeaf = !isDir;
          const accent = branchAccent(topLevelName(n));
          const ds = isDir ? dirStyle(n.depth, accent) : null;
          const fill = isLeaf
            ? colorFor(data.color_key, mode)
            : ds!.plateFill;
          const isAncestor = ancestorPaths.has(data.path);
          const baseStroke = isDir ? ds!.borderFill : "rgba(255,255,255,0.55)";
          const stroke = isAncestor ? accent : baseStroke;
          const baseStrokeWidth = isDir ? ds!.strokeWidth : 0.5;
          const strokeWidth = isAncestor
            ? Math.max(baseStrokeWidth + 0.75, 1.5)
            : baseStrokeWidth;
          const showLabel = w >= 60 && h >= 16 && !isRoot;
          const showDirHeader = isDir && h >= 28 && w >= 60 && !isRoot;
          const headerH = 14;

          const handleClick = (e: React.MouseEvent) => {
            // Synthetic / root rectangles aren't navigable.
            if (data.kind === "other" || data.kind === "hidden") return;
            if (isRoot) return;
            e.stopPropagation();
            if (isDir) onDirClick?.(data);
            else onLeafClick?.(data);
          };

          const handleContext = (e: React.MouseEvent) => {
            if (data.kind === "other" || data.kind === "hidden") return;
            if (isRoot) return;
            e.preventDefault();
            e.stopPropagation();
            const rect = containerRef.current?.getBoundingClientRect();
            const px = rect ? e.clientX - rect.left : e.clientX;
            const py = rect ? e.clientY - rect.top : e.clientY;
            onContextMenu?.(data, px, py);
          };

          const handleEnter = (e: React.MouseEvent) => {
            if (isRoot) return;
            const rect = containerRef.current?.getBoundingClientRect();
            const px = rect ? e.clientX - rect.left : e.clientX;
            const py = rect ? e.clientY - rect.top : e.clientY;
            setHoverNode(n, px, py);
          };

          const handleMove = (e: React.MouseEvent) => {
            if (isRoot) return;
            const rect = containerRef.current?.getBoundingClientRect();
            const px = rect ? e.clientX - rect.left : e.clientX;
            const py = rect ? e.clientY - rect.top : e.clientY;
            setHover((cur) =>
              cur && cur.node === n ? { ...cur, x: px, y: py } : cur,
            );
          };

          return (
            <g
              key={`${data.path}:${n.depth}`}
              onClick={handleClick}
              onContextMenu={handleContext}
              onMouseEnter={handleEnter}
              onMouseMove={handleMove}
              style={{
                cursor:
                  isRoot || data.kind === "other" || data.kind === "hidden"
                    ? "default"
                    : "pointer",
              }}
            >
              {/* 1. plate (the directory backplate or file fill) */}
              <rect
                x={x0}
                y={y0}
                width={w}
                height={h}
                fill={fill}
                stroke={stroke}
                strokeWidth={strokeWidth}
              />
              {/* 2. directory header band — drawn on top of the plate
                  so children rectangles paint over the band's
                  bottom-edge as they should. */}
              {showDirHeader && (
                <rect
                  x={x0}
                  y={y0}
                  width={w}
                  height={headerH}
                  fill={ds!.headerFill}
                  // No stroke — the plate's stroke draws the rectangle
                  // outline; the header is just a filled tab inside it.
                />
              )}
              {/* 3. label text */}
              {showDirHeader && (
                <text
                  x={x0 + 4}
                  y={y0 + 10}
                  fill="#ffffff"
                  fontSize={10}
                  fontWeight={600}
                  style={{
                    pointerEvents: "none",
                    fontFamily:
                      "ui-sans-serif, system-ui, -apple-system, sans-serif",
                  }}
                >
                  {truncate(data.name, Math.floor(w / 6))}
                </text>
              )}
              {showLabel && isLeaf && (
                <text
                  x={x0 + 4}
                  y={y0 + 13}
                  fill="white"
                  fontSize={10}
                  fontWeight={500}
                  style={{
                    pointerEvents: "none",
                    textShadow: "0 1px 2px rgba(0,0,0,0.45)",
                    fontFamily:
                      "ui-sans-serif, system-ui, -apple-system, sans-serif",
                  }}
                >
                  {truncate(data.name, Math.floor(w / 6))}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover && (
        <Tooltip
          node={hover.node.data}
          x={hover.x}
          y={hover.y}
          containerWidth={width}
          containerHeight={height}
        />
      )}
    </div>
  );
}

function truncate(s: string, max: number): string {
  if (max < 2) return "";
  if (s.length <= max) return s;
  return s.slice(0, Math.max(1, max - 1)) + "…";
}

function Tooltip({
  node, x, y, containerWidth, containerHeight,
}: {
  node: TreeNode;
  x: number;
  y: number;
  containerWidth: number;
  containerHeight: number;
}) {
  // Tooltip placement avoids spilling past container edges. Width
  // estimated; absolute container position lets us nudge.
  const tooltipW = 280;
  const tooltipH = 56;
  const left = Math.min(Math.max(x + 12, 4), containerWidth - tooltipW - 4);
  const top = Math.min(Math.max(y + 12, 4), containerHeight - tooltipH - 4);
  return (
    <div
      style={{
        position: "absolute",
        left,
        top,
        width: tooltipW,
        pointerEvents: "none",
      }}
      className="rounded-md bg-fg/90 dark:bg-surface text-bg dark:text-fg text-xs px-2.5 py-1.5 shadow-lg border border-line"
    >
      <div className="font-medium truncate">{node.name}</div>
      <div className="font-mono text-[10px] opacity-80 truncate">{node.path}</div>
      <div className="tabular-nums opacity-90 mt-0.5">
        {formatBytes(node.size_bytes)}
      </div>
    </div>
  );
}

// Local copy of the format helper to avoid pulling lib/format into a
// pure rendering component.
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}
