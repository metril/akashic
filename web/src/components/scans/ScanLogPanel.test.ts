/**
 * v0.29.7 — ScanLogPanel terminal-status badge helper.
 *
 * The full React render would require @testing-library/react +
 * jsdom; this suite stays in the node env vitest is configured for
 * and covers the pure variant-selection logic. The visual
 * verification of the rendered Badge is part of the manual smoke
 * (see plan: Verification section).
 */
import { describe, expect, it } from "vitest";

import { scanIsStoppable, terminalBadgeVariantFor } from "./ScanLogPanel";

describe("terminalBadgeVariantFor", () => {
  it("returns null for in-flight states", () => {
    expect(terminalBadgeVariantFor("running")).toBeNull();
    expect(terminalBadgeVariantFor("pending")).toBeNull();
  });

  it("returns null for nullish input", () => {
    expect(terminalBadgeVariantFor(null)).toBeNull();
    expect(terminalBadgeVariantFor(undefined)).toBeNull();
    expect(terminalBadgeVariantFor("")).toBeNull();
  });

  it("maps completed → online", () => {
    expect(terminalBadgeVariantFor("completed")).toBe("online");
  });

  it("maps failed → failed", () => {
    expect(terminalBadgeVariantFor("failed")).toBe("failed");
  });

  it("maps cancelled → neutral", () => {
    expect(terminalBadgeVariantFor("cancelled")).toBe("neutral");
  });

  it("returns null for unknown statuses (defensive)", () => {
    expect(terminalBadgeVariantFor("frobbed")).toBeNull();
  });
});

describe("scanIsStoppable", () => {
  it("is true only while the scan can still be cancelled", () => {
    expect(scanIsStoppable("running")).toBe(true);
    expect(scanIsStoppable("pending")).toBe(true);
  });

  it("is false for terminal scans — the WS staying open must not matter", () => {
    expect(scanIsStoppable("completed")).toBe(false);
    expect(scanIsStoppable("failed")).toBe(false);
    expect(scanIsStoppable("cancelled")).toBe(false);
  });

  it("is false for unknown / nullish status", () => {
    expect(scanIsStoppable(null)).toBe(false);
    expect(scanIsStoppable(undefined)).toBe(false);
    expect(scanIsStoppable("")).toBe(false);
    expect(scanIsStoppable("frobbed")).toBe(false);
  });
});
