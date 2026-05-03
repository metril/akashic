/**
 * Polar hit-test for Canvas2D sunburst arcs. Linear scan over the
 * arc list is fine at MAX_RINGS=6 × hundreds of arcs per ring; we
 * never have enough arcs to justify a spatial index.
 *
 * Coordinate convention: input (x, y) is screen-space relative to
 * the canvas top-left. (cx, cy) is the centre of the sunburst. The
 * draw module rotates the canvas so 0° points up (+y axis); we
 * mirror that rotation here so angles match the ArcSpec values
 * verbatim.
 */
import type { ArcSpec } from "./sunburstLayout";

const TWO_PI = Math.PI * 2;

/** Hit-test (x, y) against the arc list, returning the deepest
 *  matching arc (sunburst arcs nest inside each other; we want the
 *  most-specific match, which is also the highest-depth one).
 *  Returns null when (x, y) is outside the outer ring or in the
 *  centre disc. Arcs at depth=0 (the synthetic root) are skipped. */
export function hitTestArc(
  arcs: readonly ArcSpec[],
  cx: number,
  cy: number,
  x: number,
  y: number,
): ArcSpec | null {
  const dx = x - cx;
  const dy = y - cy;
  const r = Math.sqrt(dx * dx + dy * dy);
  if (r <= 0) return null;

  // Match the draw module's coordinate system: 0° = +y (up), angle
  // grows clockwise. atan2(dx, -dy) gives exactly that mapping.
  let theta = Math.atan2(dx, -dy);
  if (theta < 0) theta += TWO_PI;

  let best: ArcSpec | null = null;
  for (const a of arcs) {
    if (a.depth === 0) continue;
    if (r < a.y0 || r > a.y1) continue;
    if (theta < a.x0 || theta > a.x1) continue;
    if (!best || a.depth > best.depth) best = a;
  }
  return best;
}
