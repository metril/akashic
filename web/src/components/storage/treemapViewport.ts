/**
 * Pure viewport math for the treemap pan/wheel-zoom (Phase 9).
 *
 * Viewport is { translate: (tx, ty), scale } in CSS pixels. The shader
 * applies it as `rendered = (instance_px * scale) + translate`, so:
 *   - scale > 1 zooms in
 *   - translate offsets the rendered tree on screen
 *
 * IDENTITY = no transform; the treemap fills the container exactly as
 * d3-treemap laid it out.
 */

export interface Viewport {
  tx: number;
  ty: number;
  scale: number;
}

export const IDENTITY: Viewport = { tx: 0, ty: 0, scale: 1 };
export const SCALE_MIN = 1;
export const SCALE_MAX = 8;

/** Clamp viewport to keep at least 25% of the treemap visible at any
 *  scale, so the user can't lose it offscreen. */
export function clamp(v: Viewport, w: number, h: number): Viewport {
  const scale = Math.max(SCALE_MIN, Math.min(SCALE_MAX, v.scale));
  // At scale=s, the treemap renders w*s x h*s. We allow the user to pan
  // such that a corner is within 25% of being centered. Bounds:
  //   -((w*s - w*0.25)) <= tx <= (w*0.25)
  // (i.e., right edge can be as far left as -w*(s-0.25); left edge can
  // be as far right as w*0.25 — keeping a quarter-width strip visible.)
  const minX = -(w * scale - w * 0.25);
  const maxX = w * 0.25;
  const minY = -(h * scale - h * 0.25);
  const maxY = h * 0.25;
  return {
    scale,
    tx: Math.max(minX, Math.min(maxX, v.tx)),
    ty: Math.max(minY, Math.min(maxY, v.ty)),
  };
}

/** Scale the viewport around (cx, cy) in screen-space px. The point
 *  under the cursor stays under the cursor — standard zoom-at-cursor. */
export function zoomAt(v: Viewport, cx: number, cy: number, factor: number): Viewport {
  const newScale = Math.max(SCALE_MIN, Math.min(SCALE_MAX, v.scale * factor));
  // World point currently under (cx, cy):
  //   wx = (cx - tx) / scale
  // We want that same world point under the cursor at the new scale:
  //   cx = wx * newScale + newTx  =>  newTx = cx - wx * newScale
  const wx = (cx - v.tx) / v.scale;
  const wy = (cy - v.ty) / v.scale;
  return {
    scale: newScale,
    tx: cx - wx * newScale,
    ty: cy - wy * newScale,
  };
}

/** Convert screen-space coords to world-space (treemap layout) coords
 *  using the current viewport. Hit-test consumes world coords. */
export function screenToWorld(v: Viewport, sx: number, sy: number): { x: number; y: number } {
  return { x: (sx - v.tx) / v.scale, y: (sy - v.ty) / v.scale };
}
