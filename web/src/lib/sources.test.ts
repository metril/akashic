import { describe, it, expect } from "vitest";
import { deriveSourcePill } from "./sources";

describe("deriveSourcePill", () => {
  it("scanning status wins over everything", () => {
    expect(
      deriveSourcePill(
        { status: "scanning", last_scan_at: "2024-01-01T00:00:00Z" },
        true,
      ),
    ).toEqual({ kind: "scanning" });
  });

  it("queued (derived) wins over idle", () => {
    expect(
      deriveSourcePill(
        { status: "online", last_scan_at: "2024-01-01T00:00:00Z" },
        true,
      ),
    ).toEqual({ kind: "queued" });
  });

  it("failed status renders Failed pill", () => {
    expect(deriveSourcePill({ status: "failed", last_scan_at: null }, false)).toEqual({
      kind: "failed",
    });
  });

  it("idle online with last_scan_at renders lastScanned", () => {
    const at = "2024-01-01T00:00:00Z";
    expect(deriveSourcePill({ status: "online", last_scan_at: at }, false)).toEqual({
      kind: "lastScanned",
      at,
    });
  });

  it("idle offline with last_scan_at renders lastScanned (also drops Offline badge)", () => {
    const at = "2024-01-01T00:00:00Z";
    expect(deriveSourcePill({ status: "offline", last_scan_at: at }, false)).toEqual({
      kind: "lastScanned",
      at,
    });
  });

  it("never scanned with no last_scan_at", () => {
    expect(deriveSourcePill({ status: "online", last_scan_at: null }, false)).toEqual({
      kind: "neverScanned",
    });
  });
});
