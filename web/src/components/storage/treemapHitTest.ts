/**
 * Pure spatial hit-test for the WebGL treemap.
 *
 * The renderer doesn't keep DOM nodes per rect; we hit-test against a
 * flat array of bounds. Sorted by depth descending so the deepest rect
 * containing the cursor wins (a click on a file inside a directory
 * selects the file, not the directory).
 *
 * Linear scan is fine at the scale we render: 5k-50k rects × constant
 * compares = sub-millisecond per hit-test, well under input frame
 * budget. A real quadtree only becomes worthwhile beyond ~100k rects.
 */
import type { HierarchyRectangularNode } from "d3-hierarchy";

import type { TreeNode } from "./Treemap";

export interface HitRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  depth: number;
  node: HierarchyRectangularNode<TreeNode>;
}

export function buildHitIndex(
  layout: HierarchyRectangularNode<TreeNode>,
): HitRect[] {
  const rects: HitRect[] = [];
  for (const n of layout.descendants()) {
    rects.push({
      x0: n.x0 ?? 0,
      y0: n.y0 ?? 0,
      x1: n.x1 ?? 0,
      y1: n.y1 ?? 0,
      depth: n.depth,
      node: n,
    });
  }
  // Deepest first — first containing rect wins.
  rects.sort((a, b) => b.depth - a.depth);
  return rects;
}

export function hitTest(
  rects: HitRect[],
  x: number,
  y: number,
): HitRect | null {
  for (const r of rects) {
    if (x >= r.x0 && x < r.x1 && y >= r.y0 && y < r.y1) return r;
  }
  return null;
}
