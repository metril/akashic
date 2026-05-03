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
 * Full canvas redraw.
 *
 * v0.4.15 perf rewrite — at thousands of arcs the per-arc state
 * change cost (fillStyle/strokeStyle/lineWidth + the per-arc
 * beginPath/arc/arc/closePath) was the bottleneck (~10 canvas
 * ops × 5000 arcs × every hover frame). Two changes:
 *
 *   1. Fills use the pre-built Path2D on each ArcSpec (built
 *      once at layout time in sunburstLayout.ts). `ctx.fill(path)`
 *      doesn't re-trace the geometry.
 *   2. Fills are grouped by colour (and dimmed-vs-full alpha),
 *      so we set fillStyle once per colour-group instead of once
 *      per arc — typically a 5-20× reduction in style changes.
 *      Strokes are split into 3 lineWidth groups (default / chain
 *      / hover) with the same trick.
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

  // ── Fill pass — group by (fillColor, dim) so fillStyle/globalAlpha
  // toggles are minimised. ──────────────────────────────────────────
  type FillBucket = { fill: string; alpha: number; arcs: ArcSpec[] };
  const buckets = new Map<string, FillBucket>();
  for (const arc of arcs) {
    if (arc.depth === 0) continue;
    const dim = chainSet !== null && !chainSet.has(arc.data.path);
    const alpha = dim ? 0.5 : 1;
    const key = `${arc.fill}|${alpha}`;
    let b = buckets.get(key);
    if (!b) {
      b = { fill: arc.fill, alpha, arcs: [] };
      buckets.set(key, b);
    }
    b.arcs.push(arc);
  }
  for (const b of buckets.values()) {
    ctx.fillStyle = b.fill;
    ctx.globalAlpha = b.alpha;
    for (const arc of b.arcs) ctx.fill(arc.path);
  }
  ctx.globalAlpha = 1;

  // ── Stroke pass — three style buckets (default / chain / hover). ─
  const defaultStrokes: ArcSpec[] = [];
  const chainStrokes: ArcSpec[] = [];
  let hoverArc: ArcSpec | null = null;
  for (const arc of arcs) {
    if (arc.depth === 0) continue;
    if (arc.key === hoverKey) {
      hoverArc = arc;
    } else if (chainSet?.has(arc.data.path)) {
      chainStrokes.push(arc);
    } else {
      defaultStrokes.push(arc);
    }
  }
  if (defaultStrokes.length > 0) {
    ctx.strokeStyle = "rgba(15,23,42,0.55)";
    ctx.lineWidth = 0.5;
    for (const a of defaultStrokes) ctx.stroke(a.path);
  }
  if (chainStrokes.length > 0) {
    ctx.strokeStyle = "rgba(255,255,255,0.85)";
    ctx.lineWidth = 1.25;
    for (const a of chainStrokes) ctx.stroke(a.path);
  }
  if (hoverArc) {
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.stroke(hoverArc.path);
  }

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
