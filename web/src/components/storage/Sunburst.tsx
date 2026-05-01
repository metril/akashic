/**
 * Radial sunburst — DaisyDisk-style alternate to the rectangular
 * treemap. Same `/api/storage/tree` payload, different geometry.
 *
 * Layout:
 *   d3.partition() turns the hierarchy into (x0,x1) angular extents and
 *   (y0,y1) normalised radii in [0,1]. We map y → pixel radius. The
 *   centre disc represents the focused root; ring N is depth N.
 *
 * Visual identity is shared with the treemap via branchAccent — the
 * top-level ancestor's name selects an accent so descendants of the
 * same branch all read with the same colour family. File leaves use
 * their `color_key`-based palette (Type / Age / Owner / Risk modes
 * still work) tinted toward the branch accent for cohesion.
 *
 * Interactions:
 *   - Click an arc → drill into it (page updates ?path=…). The
 *     focused node becomes the new centre.
 *   - Click the centre → drill back up one level.
 *   - Hover an arc → onHoverChange(chain) lifts the breadcrumb to the
 *     page sidebar; the hovered arc plus its ancestor chain back to
 *     centre brighten while siblings dim.
 *   - Right-click any arc → page's context menu.
 *
 * Depth budget: cap visible rings at 6 to keep arc thickness readable.
 * Anything deeper from the focused root rolls into a `…` arc on its
 * parent ring, sized by the cumulative bytes of what we hid.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import {
  hierarchy as d3Hierarchy,
  partition as d3Partition,
  type HierarchyRectangularNode,
} from "d3-hierarchy";
import { arc as d3Arc } from "d3-shape";

import type { ColorMode } from "../../pages/StorageExplorer.types";
import { branchAccent, mix } from "./branchAccent";
import type { TreeNode } from "./Treemap";

interface SunburstProps {
  root: TreeNode;
  width: number;
  height: number;
  mode: ColorMode;
  onLeafClick?: (node: TreeNode) => void;
  onDirClick?: (node: TreeNode) => void;
  onContextMenu?: (node: TreeNode, x: number, y: number) => void;
  onHoverChange?: (chain: TreeNode[] | null) => void;
}

const MAX_RINGS = 6;
const PALETTE = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#0ea5e9",
];

function colorFor(key: string | undefined, mode: ColorMode): string {
  // Mirrors the Treemap's `colorFor` so file leaves match across views.
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
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

/** Walk up to the depth-1 ancestor (relative to whatever subtree d3
 *  was given as root). Used both for branch-accent picking and for
 *  ancestor-chain highlighting. */
function topLevelName(n: HierarchyRectangularNode<TreeNode>): string {
  let cur: HierarchyRectangularNode<TreeNode> | null = n;
  while (cur && cur.depth > 1 && cur.parent) cur = cur.parent;
  return cur?.data.name ?? "/";
}

export function Sunburst({
  root,
  width,
  height,
  mode,
  onLeafClick,
  onDirClick,
  onContextMenu,
  onHoverChange,
}: SunburstProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] =
    useState<HierarchyRectangularNode<TreeNode> | null>(null);

  // Layout: d3.partition over a hierarchy whose .sum aggregates leaf
  // sizes. We then clamp depth to MAX_RINGS, rolling the surplus into
  // a synthetic "…" arc per parent so deep trees still render.
  const layout = useMemo(() => {
    if (width <= 0 || height <= 0) return null;
    const radius = Math.min(width, height) / 2;
    if (radius <= 0) return null;

    // Truncate the tree before hierarchy() so the rolled-up node carries
    // the right total size in .sum().
    const truncated = truncateDepth(root, MAX_RINGS);

    const h = d3Hierarchy<TreeNode>(truncated, (d) => d.children)
      .sum((d) => (d.children && d.children.length > 0 ? 0 : d.size_bytes))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    d3Partition<TreeNode>().size([2 * Math.PI, radius])(h);
    return h as HierarchyRectangularNode<TreeNode>;
  }, [root, width, height]);

  const ancestorPaths = useMemo(() => {
    const set = new Set<string>();
    if (!hovered) return set;
    let cur: HierarchyRectangularNode<TreeNode> | null = hovered;
    while (cur) { set.add(cur.data.path); cur = cur.parent ?? null; }
    return set;
  }, [hovered]);

  const setHoverNode = useCallback(
    (n: HierarchyRectangularNode<TreeNode> | null) => {
      setHovered(n);
      if (!n) { onHoverChange?.(null); return; }
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

  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) / 2;
  const arcGen = d3Arc<HierarchyRectangularNode<TreeNode>>()
    .startAngle((d) => d.x0)
    .endAngle((d) => d.x1)
    .innerRadius((d) => d.y0)
    .outerRadius((d) => d.y1)
    .padAngle(0.002)
    .padRadius(radius);

  const nodes = layout.descendants();
  const rootNode = layout;
  const totalBytes = (rootNode.value ?? 0);

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full"
      onMouseLeave={() => setHoverNode(null)}
    >
      <svg
        width={width}
        height={height}
        style={{ userSelect: "none" }}
      >
        <g transform={`translate(${cx}, ${cy})`}>
          {nodes.map((n) => {
            // Skip the root (it's the centre disc, drawn separately
            // with text + click-to-zoom-out behaviour).
            if (n.depth === 0) return null;
            const data = n.data;
            const isDir =
              data.kind === "directory" || data.kind === "hidden";
            const accent = branchAccent(topLevelName(n));
            const baseColor = isDir
              ? mix(accent, "#0f172a", Math.min(0.6, 0.20 + 0.10 * (n.depth - 1)))
              : mix(colorFor(data.color_key, mode), accent, 0.15);
            const isAncestor = ancestorPaths.has(data.path);
            const isHover = hovered === n;
            const inHoverChain = isAncestor || isHover;
            const opacity = !hovered ? 1 : inHoverChain ? 1 : 0.55;
            const stroke = isHover
              ? "#ffffff"
              : isAncestor
                ? "rgba(255,255,255,0.85)"
                : "rgba(15,23,42,0.55)";
            const strokeWidth = isHover ? 2 : isAncestor ? 1.25 : 0.5;

            const handleClick = (e: React.MouseEvent) => {
              if (data.kind === "other" || data.kind === "hidden") return;
              e.stopPropagation();
              if (isDir) onDirClick?.(data);
              else onLeafClick?.(data);
            };
            const handleContext = (e: React.MouseEvent) => {
              if (data.kind === "other" || data.kind === "hidden") return;
              e.preventDefault();
              e.stopPropagation();
              const rect = containerRef.current?.getBoundingClientRect();
              const px = rect ? e.clientX - rect.left : e.clientX;
              const py = rect ? e.clientY - rect.top : e.clientY;
              onContextMenu?.(data, px, py);
            };

            const d = arcGen(n);
            if (!d) return null;
            return (
              <path
                key={`${data.path}:${n.depth}`}
                d={d}
                fill={baseColor}
                stroke={stroke}
                strokeWidth={strokeWidth}
                opacity={opacity}
                onMouseEnter={() => setHoverNode(n)}
                onClick={handleClick}
                onContextMenu={handleContext}
                style={{ cursor: "pointer", transition: "opacity 120ms" }}
              />
            );
          })}

          {/* Centre disc — focused-root summary + click-to-zoom-out */}
          <circle
            r={Math.max(0, (radius / MAX_RINGS) * 0.95)}
            fill="rgba(15,23,42,0.85)"
            stroke="rgba(255,255,255,0.15)"
            strokeWidth={1}
            onClick={(e) => {
              e.stopPropagation();
              // Page handles "go up" via the existing toolbar / ⬆ Up
              // button. Leaving the centre as a no-op click here
              // avoids two competing zoom-out paths; the cursor is a
              // pointer so the user gets the hover hint to use the
              // toolbar.
            }}
            style={{ cursor: "default" }}
          />
          <text
            textAnchor="middle"
            y={-2}
            fill="#ffffff"
            fontSize={11}
            fontWeight={600}
            style={{
              pointerEvents: "none",
              fontFamily:
                "ui-sans-serif, system-ui, -apple-system, sans-serif",
            }}
          >
            {truncate(rootNode.data.name === "/" ? "All" : rootNode.data.name, 16)}
          </text>
          <text
            textAnchor="middle"
            y={12}
            fill="rgba(255,255,255,0.75)"
            fontSize={10}
            style={{
              pointerEvents: "none",
              fontFamily:
                "ui-sans-serif, system-ui, -apple-system, sans-serif",
            }}
          >
            {formatBytes(totalBytes)}
          </text>
        </g>
      </svg>
    </div>
  );
}

/**
 * Recursively prune the input tree to at most `maxDepth` levels of
 * `children`, replacing the surplus with a synthetic `…` leaf whose
 * size is the sum of what we cut. Without this the partition's
 * outermost rings become unreadable hairlines on a deep share.
 */
function truncateDepth(node: TreeNode, maxDepth: number, depth = 0): TreeNode {
  if (!node.children || node.children.length === 0 || depth >= maxDepth) {
    return node;
  }
  if (depth + 1 >= maxDepth) {
    // One more level is allowed; collapse THAT level's grandchildren.
    return {
      ...node,
      children: node.children.map((c) =>
        c.children && c.children.length > 0
          ? rolledUpLeaf(c)
          : c,
      ),
    };
  }
  return {
    ...node,
    children: node.children.map((c) => truncateDepth(c, maxDepth, depth + 1)),
  };
}

function rolledUpLeaf(node: TreeNode): TreeNode {
  // Sum descendants once so the rollup carries the right radial weight.
  let total = 0;
  const walk = (n: TreeNode) => {
    if (!n.children || n.children.length === 0) total += n.size_bytes;
    else for (const c of n.children) walk(c);
  };
  walk(node);
  return {
    kind: node.kind,
    name: node.name,
    path: node.path,
    size_bytes: total,
    color_key: node.color_key,
    // Keep the directory as a bucket containing one rolled-up "…" leaf
    // so its arc is still visible (and clickable for drilling) on the
    // outer ring.
    children: [{
      kind: "other",
      name: "…",
      path: `${node.path}/…`,
      size_bytes: total,
      color_key: "other",
    }],
  };
}

function truncate(s: string, max: number): string {
  if (max < 2) return "";
  if (s.length <= max) return s;
  return s.slice(0, Math.max(1, max - 1)) + "…";
}

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
