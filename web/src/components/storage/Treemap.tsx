/**
 * WebGL2-rendered squarified treemap (v0.4.11). DaisyDisk / WinDirStat
 * shape: every leaf is a coloured rectangle inside its directory's
 * container, one canvas, one zoom level.
 *
 * v0.4.11 rewrite: replaces the SVG-per-node renderer (which emitted
 * 5000+ DOM nodes and re-rendered every node on every pixel of mouse
 * motion) with a single instanced WebGL draw call. Mouse-move triggers
 * no redraws — only hover-node IDENTITY changes do, and the tooltip
 * follows the cursor via imperative DOM mutation. Headroom for 50k+
 * rects at 60 fps.
 *
 * Public API unchanged from the SVG version: same Treemap props, same
 * onLeafClick / onDirClick / onContextMenu / onHoverChange callbacks,
 * same TreeNode type. StorageExplorer.tsx doesn't change.
 *
 * Visual treatment preserved:
 *   - Branch accent palette shared across descendants of a top-level dir
 *   - Depth-aware directory chrome (header band darkens; plate / border
 *     alpha steps with depth)
 *   - Hover lifts the stroke of every ancestor back to the root in the
 *     branch accent so the eye can trace the path
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  hierarchy as d3Hierarchy,
  treemap as d3Treemap,
  treemapSquarify,
  type HierarchyRectangularNode,
} from "d3-hierarchy";

import type { ColorMode } from "../../pages/StorageExplorer.types";
import { branchAccent, mix } from "./branchAccent";
import { createGLRenderer, parseColor, type GLRenderer, type RenderInstance, type Rgba } from "./treemapGL";
import { buildHitIndex, hitTest, type HitRect } from "./treemapHitTest";

export interface TreeNode {
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
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

/** Headroom on each directory rectangle for its title strip. Recursive
 *  because the nested layout needs each level's headroom subtracted from
 *  its children's available height. */
function paddingTopFor(d: HierarchyRectangularNode<TreeNode>): number {
  if (d.depth === 0) return 0;
  const h = (d.y1 ?? 0) - (d.y0 ?? 0);
  return h >= 28 ? 14 : 0;
}

/** Depth-aware directory chrome. Same math as the SVG version. */
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

function topLevelName(n: HierarchyRectangularNode<TreeNode>): string {
  let cur: HierarchyRectangularNode<TreeNode> | null = n;
  while (cur && cur.depth > 1 && cur.parent) cur = cur.parent;
  return cur?.data.name ?? "/";
}

const HEADER_H = 14;
const TRANSPARENT: Rgba = [0, 0, 0, 0];

interface LabelSpec {
  key: string;
  x: number;
  y: number;
  w: number;
  text: string;
  isHeader: boolean;
}

function truncate(s: string, max: number): string {
  if (max < 2) return "";
  if (s.length <= max) return s;
  return s.slice(0, Math.max(1, max - 1)) + "…";
}

/** Build the per-instance buffer + label overlay specs from a layout +
 *  hover state. Pure / cheap to recompute. */
function buildSceneFromLayout(
  layout: HierarchyRectangularNode<TreeNode>,
  mode: ColorMode,
  ancestorPaths: Set<string>,
): { instances: RenderInstance[]; labels: LabelSpec[] } {
  const instances: RenderInstance[] = [];
  const labels: LabelSpec[] = [];

  for (const n of layout.descendants()) {
    const x0 = n.x0 ?? 0;
    const y0 = n.y0 ?? 0;
    const x1 = n.x1 ?? 0;
    const y1 = n.y1 ?? 0;
    const w = x1 - x0;
    const h = y1 - y0;
    if (w < 1 || h < 1) continue;

    const isRoot = n.depth === 0;
    const data = n.data;
    const isDir = data.kind === "directory" || data.kind === "hidden";
    const isLeaf = !isDir;
    const accent = branchAccent(topLevelName(n));
    const ds = isDir ? dirStyle(n.depth, accent) : null;

    const fillStr = isLeaf
      ? colorFor(data.color_key, mode)
      : ds!.plateFill;
    const isAncestor = ancestorPaths.has(data.path);
    const baseStroke = isDir ? ds!.borderFill : "rgba(255,255,255,0.55)";
    const strokeStr = isAncestor ? accent : baseStroke;
    const baseStrokeWidth = isDir ? ds!.strokeWidth : 0.5;
    const strokeWidth = isAncestor
      ? Math.max(baseStrokeWidth + 0.75, 1.5)
      : baseStrokeWidth;

    instances.push({
      x: x0,
      y: y0,
      w,
      h,
      fill: parseColor(fillStr),
      stroke: parseColor(strokeStr),
      strokeWidth,
    });

    const showLabel = w >= 60 && h >= 16 && !isRoot;
    const showDirHeader = isDir && h >= 28 && w >= 60 && !isRoot;

    if (showDirHeader) {
      // Header band — second instance, no stroke.
      instances.push({
        x: x0,
        y: y0,
        w,
        h: HEADER_H,
        fill: parseColor(ds!.headerFill),
        stroke: TRANSPARENT,
        strokeWidth: 0,
      });
      labels.push({
        key: `${data.path}:dh`,
        x: x0 + 4,
        y: y0,
        w,
        text: truncate(data.name, Math.floor(w / 6)),
        isHeader: true,
      });
    }
    if (showLabel && isLeaf) {
      labels.push({
        key: `${data.path}:lf`,
        x: x0 + 4,
        y: y0,
        w,
        text: truncate(data.name, Math.floor(w / 6)),
        isHeader: false,
      });
    }
  }

  return { instances, labels };
}

function buildAncestorPaths(
  hover: HierarchyRectangularNode<TreeNode> | null,
): Set<string> {
  const set = new Set<string>();
  let cur: HierarchyRectangularNode<TreeNode> | null = hover;
  while (cur) {
    set.add(cur.data.path);
    cur = cur.parent ?? null;
  }
  return set;
}

function buildChain(n: HierarchyRectangularNode<TreeNode>): TreeNode[] {
  const chain: TreeNode[] = [];
  let cur: HierarchyRectangularNode<TreeNode> | null = n;
  while (cur) {
    chain.unshift(cur.data);
    cur = cur.parent ?? null;
  }
  return chain;
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

  const hitIndex: HitRect[] = useMemo(
    () => (layout ? buildHitIndex(layout) : []),
    [layout],
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const glRef = useRef<GLRenderer | null>(null);
  const [glFailed, setGlFailed] = useState(false);
  const [hoverNode, setHoverNode] = useState<HierarchyRectangularNode<TreeNode> | null>(
    null,
  );

  // Init / dispose the WebGL renderer once per canvas mount.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = createGLRenderer(canvas);
    if (!renderer) {
      setGlFailed(true);
      return;
    }
    glRef.current = renderer;
    return () => {
      renderer.dispose();
      glRef.current = null;
    };
  }, []);

  const ancestorPaths = useMemo(
    () => buildAncestorPaths(hoverNode),
    [hoverNode],
  );

  const { instances, labels } = useMemo(() => {
    if (!layout) return { instances: [], labels: [] };
    return buildSceneFromLayout(layout, mode, ancestorPaths);
  }, [layout, mode, ancestorPaths]);

  // Resize + redraw whenever any input changes.
  useEffect(() => {
    if (!glRef.current) return;
    glRef.current.resize(width, height);
    glRef.current.draw(instances);
  }, [instances, width, height]);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Imperative tooltip position update — does NOT trigger a React
      // render, so mouse-move stays free of reconciliation cost.
      const tooltip = tooltipRef.current;
      if (tooltip) {
        // Bias toward the bottom-right of the cursor; clamp so we
        // never spill past the container edge.
        const tw = tooltip.offsetWidth || 280;
        const th = tooltip.offsetHeight || 56;
        const left = Math.min(Math.max(x + 12, 4), width - tw - 4);
        const top = Math.min(Math.max(y + 12, 4), height - th - 4);
        tooltip.style.transform = `translate3d(${left}px, ${top}px, 0)`;
      }

      const hit = hitTest(hitIndex, x, y);
      const next = hit?.node ?? null;
      // Only setState — and re-render — when the hovered node identity
      // changes. With the prior SVG impl, mouse-move set state on every
      // pixel, re-rendering all 5000 nodes per movement.
      if (next !== hoverNode) {
        setHoverNode(next);
        onHoverChange?.(next ? buildChain(next) : null);
      }
    },
    [hitIndex, hoverNode, onHoverChange, width, height],
  );

  const handleMouseLeave = useCallback(() => {
    if (hoverNode !== null) {
      setHoverNode(null);
      onHoverChange?.(null);
    }
  }, [hoverNode, onHoverChange]);

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!hoverNode) return;
      const data = hoverNode.data;
      if (data.kind === "other" || data.kind === "hidden") return;
      if (hoverNode.depth === 0) return;
      e.stopPropagation();
      if (data.kind === "directory") onDirClick?.(data);
      else onLeafClick?.(data);
    },
    [hoverNode, onDirClick, onLeafClick],
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      if (!hoverNode) return;
      const data = hoverNode.data;
      if (data.kind === "other" || data.kind === "hidden") return;
      if (hoverNode.depth === 0) return;
      e.preventDefault();
      e.stopPropagation();
      const container = containerRef.current;
      const rect = container?.getBoundingClientRect();
      const px = rect ? e.clientX - rect.left : e.clientX;
      const py = rect ? e.clientY - rect.top : e.clientY;
      onContextMenu?.(data, px, py);
    },
    [hoverNode, onContextMenu],
  );

  if (glFailed) {
    return (
      <div
        ref={containerRef}
        className="w-full h-full flex items-center justify-center text-fg-subtle text-sm p-4 text-center"
      >
        Treemap unavailable — your browser doesn&apos;t support WebGL2.
        Try the Sunburst view or open the Browse page.
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full select-none"
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
      onContextMenu={handleContextMenu}
      style={{
        cursor: hoverNode && hoverNode.depth > 0 ? "pointer" : "default",
      }}
    >
      <canvas
        ref={canvasRef}
        className="block"
        style={{ width, height, display: "block" }}
      />
      {/* Label overlay — pointer-events-none so the canvas still
          receives all mouse events. Memoized via the labels array
          identity so React reconciles only when layout changes. */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ width, height }}
      >
        {labels.map((l) => (
          <span
            key={l.key}
            style={{
              position: "absolute",
              left: l.x,
              top: l.y + (l.isHeader ? 1 : 3),
              maxWidth: l.w - 4,
              color: "#ffffff",
              fontSize: 10,
              fontWeight: l.isHeader ? 600 : 500,
              fontFamily:
                "ui-sans-serif, system-ui, -apple-system, sans-serif",
              textShadow: l.isHeader ? "none" : "0 1px 2px rgba(0,0,0,0.45)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              lineHeight: "12px",
            }}
          >
            {l.text}
          </span>
        ))}
      </div>
      {/* Tooltip — always mounted; opacity toggles. Position is
          updated imperatively on mouse-move, so React never sees a
          tooltip render during cursor motion. Content re-renders only
          when hoverNode changes. */}
      <div
        ref={tooltipRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 280,
          opacity: hoverNode ? 1 : 0,
          pointerEvents: "none",
          transition: "opacity 80ms linear",
        }}
        className="rounded-md bg-fg/90 dark:bg-surface text-bg dark:text-fg text-xs px-2.5 py-1.5 shadow-lg border border-line"
      >
        {hoverNode && (
          <>
            <div className="font-medium truncate">{hoverNode.data.name}</div>
            <div className="font-mono text-[10px] opacity-80 truncate">
              {hoverNode.data.path}
            </div>
            <div className="tabular-nums opacity-90 mt-0.5">
              {formatBytes(hoverNode.data.size_bytes)}
            </div>
          </>
        )}
      </div>
    </div>
  );
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
