/**
 * Shared per-top-level-branch accent palette.
 *
 * Both the Treemap and the Sunburst hash a directory's name into the
 * palette so descendants of the same top-level branch share an accent.
 * The eye can then trace "everything inside Gintama" by colour without
 * the page having to render explicit borders or labels.
 *
 * `mix` blends an accent toward a target colour by alpha — used to
 * darken header bands as you nest deeper, so a depth-1 directory's
 * header is bright accent, depth-3's is mostly slate.
 */

import { CATEGORY_PALETTE } from "./categoryPalette";

export function branchAccent(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return CATEGORY_PALETTE[Math.abs(h) % CATEGORY_PALETTE.length];
}

function hexToRgb(hex: string): [number, number, number] {
  const v = hex.replace("#", "");
  const n = parseInt(v.length === 3
    ? v.split("").map((c) => c + c).join("")
    : v, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Linear-space-ignorant blend; good enough for chrome-tinting. */
export function mix(a: string, b: string, t: number): string {
  const [ar, ag, ab] = hexToRgb(a);
  const [br, bg, bb] = hexToRgb(b);
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}
