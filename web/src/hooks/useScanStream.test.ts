/**
 * Pure-function tests for the v0.33.0 useScanStream rewrite — the line
 * merge helpers that back the full-history log viewer. The React-side
 * behaviour (page-loop, WS lifecycle) is exercised by the manual smoke in
 * the v0.33.0 plan; the project has no @testing-library/react set up.
 *
 * Invariants worth pinning:
 *   1. mergeLines dedupes by id — overlapping history pages / reconnect
 *      backfills must not double-insert a row.
 *   2. mergeLines returns a (ts, id)-ordered array regardless of the
 *      order rows arrive in.
 *   3. appendLines dedupes by id but keeps arrival order (live tail).
 */
import { describe, expect, it } from "vitest";

import { appendLines, mergeLines } from "./useScanStream";
import type { ScanLogLine } from "../types";

function line(id: string, ts: string): ScanLogLine {
  return { id, ts, level: "info", message: id };
}

describe("mergeLines", () => {
  it("dedupes by id across overlapping pages", () => {
    const a = [line("1", "2026-05-18T00:00:01Z"), line("2", "2026-05-18T00:00:02Z")];
    const b = [line("2", "2026-05-18T00:00:02Z"), line("3", "2026-05-18T00:00:03Z")];
    const merged = mergeLines(a, b);
    expect(merged.map((l) => l.id)).toEqual(["1", "2", "3"]);
  });

  it("returns a (ts, id)-ordered array no matter the arrival order", () => {
    const existing = [line("c", "2026-05-18T00:00:03Z")];
    const incoming = [
      line("a", "2026-05-18T00:00:01Z"),
      line("b", "2026-05-18T00:00:02Z"),
    ];
    const merged = mergeLines(existing, incoming);
    expect(merged.map((l) => l.id)).toEqual(["a", "b", "c"]);
  });

  it("breaks ts ties by id so a shared timestamp is stably ordered", () => {
    const ts = "2026-05-18T00:00:01Z";
    const merged = mergeLines([line("z", ts)], [line("a", ts), line("m", ts)]);
    expect(merged.map((l) => l.id)).toEqual(["a", "m", "z"]);
  });

  it("returns the same reference when nothing is fresh", () => {
    const existing = [line("1", "2026-05-18T00:00:01Z")];
    expect(mergeLines(existing, [line("1", "2026-05-18T00:00:01Z")])).toBe(existing);
  });
});

describe("appendLines", () => {
  it("appends fresh lines in arrival order", () => {
    const existing = [line("1", "2026-05-18T00:00:01Z")];
    const merged = appendLines(existing, [
      line("2", "2026-05-18T00:00:02Z"),
      line("3", "2026-05-18T00:00:03Z"),
    ]);
    expect(merged.map((l) => l.id)).toEqual(["1", "2", "3"]);
  });

  it("drops a line whose id is already buffered (WS / backfill overlap)", () => {
    const existing = [line("1", "2026-05-18T00:00:01Z")];
    const merged = appendLines(existing, [line("1", "2026-05-18T00:00:01Z")]);
    expect(merged).toBe(existing);
  });
});
