/**
 * Canvas2D renderer for the v0.4.14 sunburst. Single full-canvas
 * redraw per call — cheap at the arc counts the layout cap allows
 * (MAX_RINGS=6 × at most a couple hundred arcs per ring). Hover
 * changes invoke this directly without going through React, so the
 * mouse-move path stays free of reconciliation cost.
 */
import type { ArcSpec } from "./sunburstLayout";

const TWO_PI = Math.PI * 2;

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

function truncate(s: string, max: number): string {
  if (max < 2) return "";
  if (s.length <= max) return s;
  return s.slice(0, Math.max(1, max - 1)) + "…";
}

/** Resize the backing canvas for the given CSS dims at the current
 *  devicePixelRatio. Keeps draw-time DPR scaling separate so callers
 *  can re-resize without redrawing. */
export function sizeCanvas(canvas: HTMLCanvasElement, widthCss: number, heightCss: number): void {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(widthCss * dpr));
  canvas.height = Math.max(1, Math.round(heightCss * dpr));
  canvas.style.width = `${widthCss}px`;
  canvas.style.height = `${heightCss}px`;
}

interface DrawArgs {
  canvas: HTMLCanvasElement;
  arcs: readonly ArcSpec[];
  hoverKey: string | null;
  /** Centre of the sunburst in CSS pixels. */
  cx: number;
  cy: number;
  /** Outer radius in CSS pixels — used to size the centre-disc + cap
   *  the visible region. */
  radius: number;
}

/**
 * Full canvas redraw. Strategy:
 *   1. Translate to (cx, cy) and pre-rotate so 0° points up.
 *   2. For each arc with depth > 0: fill + stroke the wedge at
 *      whatever opacity hover dictates (chain = full bright,
 *      siblings dim).
 *   3. Centre disc + name + total bytes.
 *
 * Hover treatment: when there's a hover, the chain ancestors render
 * at full alpha with a brighter stroke; siblings drop to 0.55 alpha.
 * No CSS transitions — same recipe as the v0.4.13 Drawer fix.
 */
export function drawSunburst({ canvas, arcs, hoverKey, cx, cy, radius }: DrawArgs): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;

  // Reset transform for the clear, then re-establish DPR-scaled
  // transform so all subsequent ops are in CSS pixels.
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Compute the hover chain set once. Empty when no hover.
  let chainSet: Set<string> | null = null;
  if (hoverKey) {
    const hit = arcs.find((a) => a.key === hoverKey) ?? null;
    if (hit) chainSet = new Set(hit.ancestorPaths);
  }

  ctx.translate(cx, cy);

  for (const arc of arcs) {
    if (arc.depth === 0) continue;
    const a0 = arc.x0;
    const a1 = arc.x1;
    if (a1 - a0 < 0.001) continue;
    const r0 = arc.y0;
    const r1 = arc.y1;
    if (r1 - r0 < 0.5) continue;

    const isHover = arc.key === hoverKey;
    const inChain = chainSet?.has(arc.data.path) ?? false;
    const dim = chainSet !== null && !inChain;

    // Convert d3-partition's [0, 2π] from-noon angles into the
    // canvas convention. We rotate the canvas via the arc draw
    // calls themselves: angle θ from "noon" → canvas angle
    // (θ - π/2). Equivalent to `ctx.rotate(-π/2)` but we keep it
    // local so the centre-disc text isn't rotated.
    const ca0 = a0 - Math.PI / 2;
    const ca1 = a1 - Math.PI / 2;

    ctx.beginPath();
    ctx.arc(0, 0, r1, ca0, ca1, false);
    ctx.arc(0, 0, r0, ca1, ca0, true);
    ctx.closePath();

    ctx.fillStyle = arc.fill;
    ctx.globalAlpha = dim ? 0.5 : 1;
    ctx.fill();

    ctx.lineWidth = isHover ? 2 : inChain ? 1.25 : 0.5;
    ctx.strokeStyle = isHover
      ? "#ffffff"
      : inChain
        ? "rgba(255,255,255,0.85)"
        : "rgba(15,23,42,0.55)";
    ctx.stroke();
  }

  ctx.globalAlpha = 1;

  // Centre disc + label.
  const root = arcs[0];
  const innerRadius = Math.max(0, (radius / 6) * 0.95);  // matches MAX_RINGS=6
  ctx.beginPath();
  ctx.arc(0, 0, innerRadius, 0, TWO_PI);
  ctx.fillStyle = "rgba(15,23,42,0.85)";
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(255,255,255,0.15)";
  ctx.stroke();

  if (root) {
    ctx.font = "600 11px ui-sans-serif, system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    const name = root.data.name === "/" ? "All" : root.data.name;
    ctx.fillText(truncate(name, 16), 0, -2);

    ctx.font = "500 10px ui-sans-serif, system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.75)";
    ctx.textBaseline = "top";
    ctx.fillText(formatBytes(rootTotal(arcs)), 0, 4);
  }

  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

function rootTotal(arcs: readonly ArcSpec[]): number {
  // The root's value isn't stored on ArcSpec directly; sum the
  // depth-1 children to recover it. Cheap (small N).
  let total = 0;
  for (const a of arcs) {
    if (a.depth === 1) total += a.data.size_bytes;
  }
  return total;
}
