/**
 * Pure helpers for the treemap drill animation.
 *
 * runAnim drives a rAF loop with eased progress. Match logic builds
 * (key -> RenderInstance) maps for old + new scenes, classifies each
 * key as matched / entering / exiting, and produces interpolated
 * instances per frame. The Treemap React shell calls this from a
 * useEffect that fires when the layout changes; mouse-move and hover
 * never trigger animation.
 */

import type { Rgba, RenderInstance } from "./treemapGL";

export type EasingFn = (t: number) => number;

export const easeOutCubic: EasingFn = (t) => 1 - Math.pow(1 - t, 3);

export interface AnimSpec {
  /** total duration in ms */
  durationMs: number;
  /** called per frame with eased progress in [0, 1] */
  onFrame: (eased: number) => void;
  /** optional easing override (default: cubic-out) */
  easing?: EasingFn;
  /** called once at the end (eased == 1 frame already drawn) */
  onDone?: () => void;
}

/** Run a rAF loop. Returns a cancel fn that stops the animation
 *  immediately. Cancel during a frame is safe — the in-flight tick
 *  short-circuits before invoking onFrame. */
export function runAnim(spec: AnimSpec): () => void {
  let cancelled = false;
  const start = performance.now();
  const easing = spec.easing ?? easeOutCubic;

  function tick(now: number) {
    if (cancelled) return;
    const raw = Math.min(1, (now - start) / spec.durationMs);
    spec.onFrame(easing(raw));
    if (raw < 1) {
      requestAnimationFrame(tick);
    } else {
      spec.onDone?.();
    }
  }

  requestAnimationFrame(tick);
  return () => {
    cancelled = true;
  };
}

/** Per-instance pair for animation: matched / entering / exiting. */
export interface InstancePair {
  /** instance to show at progress=0 (null for entering) */
  from: RenderInstance | null;
  /** instance to show at progress=1 (null for exiting) */
  to: RenderInstance | null;
}

/** Match keyed instances by key. Order of the result is stable on
 *  the union of old-then-new keys, so subsequent draws are
 *  deterministic. */
export function matchInstances(
  oldInstances: readonly RenderInstance[],
  oldKeys: readonly string[],
  newInstances: readonly RenderInstance[],
  newKeys: readonly string[],
): InstancePair[] {
  const oldByKey = new Map<string, RenderInstance>();
  for (let i = 0; i < oldInstances.length; i++) {
    oldByKey.set(oldKeys[i], oldInstances[i]);
  }
  const newByKey = new Map<string, RenderInstance>();
  for (let i = 0; i < newInstances.length; i++) {
    newByKey.set(newKeys[i], newInstances[i]);
  }

  const pairs: InstancePair[] = [];
  // Matched + exiting come from oldKeys order.
  const seen = new Set<string>();
  for (const key of oldKeys) {
    const from = oldByKey.get(key)!;
    const to = newByKey.get(key) ?? null;
    pairs.push({ from, to });
    seen.add(key);
  }
  // Entering: keys only in new.
  for (const key of newKeys) {
    if (seen.has(key)) continue;
    pairs.push({ from: null, to: newByKey.get(key)! });
  }
  return pairs;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function lerpRgba(a: Rgba, b: Rgba, t: number): Rgba {
  return [
    lerp(a[0], b[0], t),
    lerp(a[1], b[1], t),
    lerp(a[2], b[2], t),
    lerp(a[3], b[3], t),
  ];
}

/** Build the per-frame instance buffer from matched pairs at a given
 *  eased progress in [0, 1]. */
export function interpolatePairs(
  pairs: readonly InstancePair[],
  t: number,
): RenderInstance[] {
  const out: RenderInstance[] = [];
  for (const p of pairs) {
    if (p.from && p.to) {
      // Matched — lerp bounds + colors.
      out.push({
        x: lerp(p.from.x, p.to.x, t),
        y: lerp(p.from.y, p.to.y, t),
        w: lerp(p.from.w, p.to.w, t),
        h: lerp(p.from.h, p.to.h, t),
        fill: lerpRgba(p.from.fill, p.to.fill, t),
        stroke: lerpRgba(p.from.stroke, p.to.stroke, t),
        strokeWidth: lerp(p.from.strokeWidth, p.to.strokeWidth, t),
      });
    } else if (p.to) {
      // Entering — fade in at the new position. Hold the new bounds
      // (no scale-up); just ramp alpha. Cheaper than tracking a
      // per-key entry origin and visually equivalent at 300ms.
      out.push({
        x: p.to.x,
        y: p.to.y,
        w: p.to.w,
        h: p.to.h,
        fill: [p.to.fill[0], p.to.fill[1], p.to.fill[2], p.to.fill[3] * t],
        stroke: [p.to.stroke[0], p.to.stroke[1], p.to.stroke[2], p.to.stroke[3] * t],
        strokeWidth: p.to.strokeWidth,
      });
    } else if (p.from) {
      // Exiting — fade out at the old position.
      const inv = 1 - t;
      out.push({
        x: p.from.x,
        y: p.from.y,
        w: p.from.w,
        h: p.from.h,
        fill: [p.from.fill[0], p.from.fill[1], p.from.fill[2], p.from.fill[3] * inv],
        stroke: [p.from.stroke[0], p.from.stroke[1], p.from.stroke[2], p.from.stroke[3] * inv],
        strokeWidth: p.from.strokeWidth,
      });
    }
  }
  return out;
}
