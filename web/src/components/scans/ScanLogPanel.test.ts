/**
 * Pure-logic coverage for the scan Live Log panel (v0.32.0).
 *
 * The panel's render, auto-follow and WebSocket watchdog need a DOM /
 * React renderer; this suite stays in the node env vitest is
 * configured for and covers the pure helpers in `scanLog.ts`. Visual
 * behaviour is part of the manual smoke (see plan: Verification).
 */
import { describe, expect, it } from "vitest";

import type { ScanLogLine } from "../../types";
import {
  filterLogLines,
  isStreamStale,
  scanIsStoppable,
  terminalBadgeVariantFor,
} from "./scanLog";

function line(p: Partial<ScanLogLine> & { id: string }): ScanLogLine {
  return {
    id: p.id,
    ts: p.ts ?? "2026-05-17T00:00:00.000Z",
    level: p.level ?? "info",
    message: p.message ?? "",
    scanner_id: p.scanner_id ?? null,
    scanner_name: p.scanner_name ?? null,
  };
}

describe("terminalBadgeVariantFor", () => {
  it("returns null for in-flight states", () => {
    expect(terminalBadgeVariantFor("running")).toBeNull();
    expect(terminalBadgeVariantFor("pending")).toBeNull();
  });

  it("returns null for nullish / unknown input", () => {
    expect(terminalBadgeVariantFor(null)).toBeNull();
    expect(terminalBadgeVariantFor(undefined)).toBeNull();
    expect(terminalBadgeVariantFor("")).toBeNull();
    expect(terminalBadgeVariantFor("frobbed")).toBeNull();
  });

  it("maps terminal states to badge variants", () => {
    expect(terminalBadgeVariantFor("completed")).toBe("online");
    expect(terminalBadgeVariantFor("failed")).toBe("failed");
    expect(terminalBadgeVariantFor("cancelled")).toBe("neutral");
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

describe("filterLogLines", () => {
  const lines: ScanLogLine[] = [
    line({ id: "1", level: "info", message: "walk starting" }),
    line({ id: "2", level: "warn", message: "slow share" }),
    line({ id: "3", level: "error", message: "permission denied" }),
    line({ id: "4", level: "stderr", message: "raw library noise" }),
  ];
  const noQuery = "";
  const noScanners: ReadonlySet<string> = new Set();

  it("includes only the enabled levels", () => {
    const out = filterLogLines(lines, {
      levels: new Set(["info", "warn", "error"]),
      query: noQuery,
      scanners: noScanners,
    });
    expect(out.map((l) => l.id)).toEqual(["1", "2", "3"]);
  });

  it("excludes stderr by default (it is not in the default level set)", () => {
    const out = filterLogLines(lines, {
      levels: new Set(["info", "warn", "error"]),
      query: noQuery,
      scanners: noScanners,
    });
    expect(out.some((l) => l.level === "stderr")).toBe(false);
  });

  it("shows stderr once its level is enabled", () => {
    const out = filterLogLines(lines, {
      levels: new Set(["stderr"]),
      query: noQuery,
      scanners: noScanners,
    });
    expect(out.map((l) => l.id)).toEqual(["4"]);
  });

  it("an errors-only view is just the error level", () => {
    const out = filterLogLines(lines, {
      levels: new Set(["error"]),
      query: noQuery,
      scanners: noScanners,
    });
    expect(out.map((l) => l.id)).toEqual(["3"]);
  });

  it("search is a case-insensitive substring match on the message", () => {
    const out = filterLogLines(lines, {
      levels: new Set(["info", "warn", "error", "stderr"]),
      query: "DENIED",
      scanners: noScanners,
    });
    expect(out.map((l) => l.id)).toEqual(["3"]);
  });

  it("filters by scanner when a scanner set is given", () => {
    const tagged: ScanLogLine[] = [
      line({ id: "a", scanner_id: "s1", message: "from one" }),
      line({ id: "b", scanner_id: "s2", message: "from two" }),
      line({ id: "c", scanner_id: null, message: "unattributed" }),
    ];
    const out = filterLogLines(tagged, {
      levels: new Set(["info"]),
      query: "",
      scanners: new Set(["s1"]),
    });
    // Only s1's line — an unattributed line is excluded once a scanner
    // filter is active.
    expect(out.map((l) => l.id)).toEqual(["a"]);
  });

  it("an empty scanner set means every scanner", () => {
    const tagged: ScanLogLine[] = [
      line({ id: "a", scanner_id: "s1" }),
      line({ id: "b", scanner_id: "s2" }),
    ];
    const out = filterLogLines(tagged, {
      levels: new Set(["info"]),
      query: "",
      scanners: new Set(),
    });
    expect(out.map((l) => l.id)).toEqual(["a", "b"]);
  });

  it("combines level, search and scanner filters", () => {
    const mixed: ScanLogLine[] = [
      line({ id: "1", level: "error", scanner_id: "s1", message: "disk full" }),
      line({ id: "2", level: "error", scanner_id: "s2", message: "disk full" }),
      line({ id: "3", level: "info", scanner_id: "s1", message: "disk full" }),
    ];
    const out = filterLogLines(mixed, {
      levels: new Set(["error"]),
      query: "disk",
      scanners: new Set(["s1"]),
    });
    expect(out.map((l) => l.id)).toEqual(["1"]);
  });
});

describe("isStreamStale", () => {
  const STALE_MS = 45_000;

  it("is not stale before the first frame (null activity)", () => {
    expect(isStreamStale(null, 1_000_000, STALE_MS)).toBe(false);
  });

  it("is not stale within the window — a ping counts as activity", () => {
    const ping = 1_000_000;
    expect(isStreamStale(ping, ping, STALE_MS)).toBe(false);
    expect(isStreamStale(ping, ping + 44_000, STALE_MS)).toBe(false);
  });

  it("is stale once the silence exceeds the window", () => {
    const lastFrame = 1_000_000;
    expect(isStreamStale(lastFrame, lastFrame + 46_000, STALE_MS)).toBe(true);
  });
});
