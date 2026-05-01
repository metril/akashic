/**
 * Shared types for the Storage Explorer page and its sub-components.
 * Lives outside the .tsx so the Treemap component can import the
 * `ColorMode` enum without pulling in React entry-points.
 */

export type ColorMode = "type" | "age" | "owner" | "risk";
