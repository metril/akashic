/**
 * v0.5.11 — single source of truth for treemap / sunburst / branch
 * accent colors. The same hex values are mirrored in
 * `tailwind.config.js` under `colors.category.{1..10}`,
 * `colors.heat.*`, and `colors.risk.*` so HTML chrome (legends,
 * dot indicators) can use class names while the WebGL canvas
 * consumes the hex strings directly.
 *
 * Edit either side and update the other — keeping them in sync is
 * cheap (10 colors total) and gives both worlds one palette.
 */

export const CATEGORY_PALETTE = [
  "#6366f1", // category-1
  "#10b981", // category-2
  "#f59e0b", // category-3
  "#ef4444", // category-4
  "#8b5cf6", // category-5
  "#06b6d4", // category-6
  "#ec4899", // category-7
  "#84cc16", // category-8
  "#f97316", // category-9
  "#0ea5e9", // category-10
] as const;

export const HEAT_COLORS = {
  hot: "#10b981",
  warm: "#f59e0b",
  cold: "#94a3b8",
} as const;

export const RISK_COLORS = {
  public: "#ef4444",
  authenticated: "#f59e0b",
  restricted: "#10b981",
} as const;

/** Slate-500 — used by treemap "Other" / directory placeholder cells. */
export const NEUTRAL_CELL = "#94a3b8";
/** Slate-600 — directory border accent. */
export const NEUTRAL_DIRECTORY = "#475569";

export function categoryColorForKey(key: string | undefined): string {
  if (!key) return CATEGORY_PALETTE[0];
  let h = 0;
  for (let i = 0; i < key.length; i++) {
    h = (h * 31 + key.charCodeAt(i)) | 0;
  }
  return CATEGORY_PALETTE[Math.abs(h) % CATEGORY_PALETTE.length];
}
