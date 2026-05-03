/**
 * Pure layout for the v0.4.14 Canvas2D sunburst. Replaces the inline
 * d3-hierarchy + per-render arc-d generation that lived in Sunburst.tsx
 * pre-v0.4.14. The component now consumes flat ArcSpec[] and stays
 * lean: hover changes don't re-run any of this.
 */
import {
  hierarchy as d3Hierarchy,
  partition as d3Partition,
  type HierarchyRectangularNode,
} from "d3-hierarchy";

import type { ColorMode } from "../../pages/StorageExplorer.types";
import { branchAccent, mix } from "./branchAccent";
import type { TreeNode } from "./Treemap";

export const MAX_RINGS = 6;

const PALETTE = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#0ea5e9",
];

export function colorFor(key: string | undefined, mode: ColorMode): string {
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

function topLevelName(n: HierarchyRectangularNode<TreeNode>): string {
  let cur: HierarchyRectangularNode<TreeNode> | null = n;
  while (cur && cur.depth > 1 && cur.parent) cur = cur.parent;
  return cur?.data.name ?? "/";
}

/**
 * One renderable arc. Angles in radians (d3-partition convention:
 * [0, 2π], 0 at +y / "12 o'clock" once we apply the canvas rotation).
 * Radii in pixels relative to the centre.
 */
export interface ArcSpec {
  key: string;
  data: TreeNode;
  depth: number;
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  fill: string;
  /** v0.4.15 — pre-built Path2D in centre-relative pixel coords. The
   *  draw module fills/strokes via `ctx.fill(path)` / `ctx.stroke(path)`,
   *  so per-redraw geometry tracing (which dominated frame time at
   *  thousands of arcs) is gone. Built once per layout. */
  path: Path2D;
  /** Path strings of every ancestor (root → this node), used by both
   *  hover-chain highlighting and the page sidebar's lift. */
  ancestorPaths: string[];
  /** Names of every ancestor (root → this node), used to build the
   *  HoverSidebar breadcrumb without forcing the component to walk
   *  the d3 hierarchy itself. */
  chain: TreeNode[];
}

/**
 * Recursively prune the input tree to at most `maxDepth` levels of
 * `children`, replacing the surplus with a synthetic `…` leaf whose
 * size is the sum of what we cut. Identical to the v0.4.13 behaviour
 * — the outer rings stay readable on a deep share.
 */
export function truncateDepth(node: TreeNode, maxDepth: number, depth = 0): TreeNode {
  if (!node.children || node.children.length === 0 || depth >= maxDepth) {
    return node;
  }
  if (depth + 1 >= maxDepth) {
    return {
      ...node,
      children: node.children.map((c) =>
        c.children && c.children.length > 0 ? rolledUpLeaf(c) : c,
      ),
    };
  }
  return {
    ...node,
    children: node.children.map((c) => truncateDepth(c, maxDepth, depth + 1)),
  };
}

function rolledUpLeaf(node: TreeNode): TreeNode {
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
    children: [{
      kind: "other",
      name: "…",
      path: `${node.path}/…`,
      size_bytes: total,
      color_key: "other",
    }],
  };
}

/**
 * Build the flat arc list. Caller passes `radius` (already
 * `min(width, height) / 2`); we map d3-partition's normalised radii
 * to pixel values here so downstream code (draw + hit-test) doesn't
 * need to know about the layout step.
 *
 * The first entry in the returned list is always the root (depth=0)
 * so the centre disc renderer can read its name + total bytes
 * without re-walking the source tree.
 */
export function buildArcLayout(
  root: TreeNode,
  mode: ColorMode,
  radius: number,
): ArcSpec[] {
  if (radius <= 0) return [];
  const truncated = truncateDepth(root, MAX_RINGS);
  const h = d3Hierarchy<TreeNode>(truncated, (d) => d.children)
    .sum((d) => (d.children && d.children.length > 0 ? 0 : (d.layout_weight ?? d.size_bytes)))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const layout = d3Partition<TreeNode>().size([2 * Math.PI, radius])(h);

  const arcs: ArcSpec[] = [];
  for (const n of layout.descendants()) {
    const data = n.data;
    const isDir = data.kind === "directory" || data.kind === "hidden";
    // v0.4.16 — childless directories (synthetic source nodes; real
    // directories at the depth-cutoff) get the leaf colorization.
    // Without this they'd be dim-plate-mixed-with-#0f172a, which on
    // a small slice is nearly invisible.
    const hasChildren = (data.children?.length ?? 0) > 0;
    const isLeafLike = !isDir || !hasChildren;
    const accent = branchAccent(topLevelName(n));
    const baseColor = isLeafLike
      ? mix(colorFor(data.color_key, mode), accent, 0.15)
      : mix(accent, "#0f172a", Math.min(0.6, 0.20 + 0.10 * (n.depth - 1)));

    // Walk the ancestor chain once so both highlight queries and
    // sidebar lifts have O(1) access.
    const ancestorPaths: string[] = [];
    const chain: TreeNode[] = [];
    let cur: HierarchyRectangularNode<TreeNode> | null = n;
    while (cur) {
      ancestorPaths.push(cur.data.path);
      chain.unshift(cur.data);
      cur = cur.parent ?? null;
    }

    arcs.push({
      key: `${data.path}:${n.depth}`,
      data,
      depth: n.depth,
      x0: n.x0,
      x1: n.x1,
      y0: n.y0,
      y1: n.y1,
      fill: baseColor,
      path: buildArcPath2D(n.x0, n.x1, n.y0, n.y1),
      ancestorPaths,
      chain,
    });
  }
  return arcs;
}

/** Build a Path2D for a single arc wedge in centre-relative pixel
 *  coords (callers translate to the centre before drawing). The
 *  canvas convention here matches sunburstDraw: 0° at "12 o'clock"
 *  (+y up), angles grow clockwise. d3-partition emits angles from
 *  noon as [0, 2π], so we subtract π/2 to map into the canvas's
 *  natural angle convention. Skips degenerate wedges. */
function buildArcPath2D(x0: number, x1: number, y0: number, y1: number): Path2D {
  const path = new Path2D();
  if (x1 - x0 < 0.001 || y1 - y0 < 0.5) return path;
  const ca0 = x0 - Math.PI / 2;
  const ca1 = x1 - Math.PI / 2;
  path.arc(0, 0, y1, ca0, ca1, false);
  path.arc(0, 0, y0, ca1, ca0, true);
  path.closePath();
  return path;
}
