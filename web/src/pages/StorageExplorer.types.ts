/**
 * Shared types for the Storage Explorer page and its sub-components.
 * Lives outside the .tsx so the Treemap / Sunburst components can
 * import these without pulling in React entry-points.
 */

export type ColorMode = "type" | "age" | "owner" | "risk";

export type LayoutMode = "treemap" | "sunburst";
