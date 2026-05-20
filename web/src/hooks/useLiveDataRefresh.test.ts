/**
 * Pure-function tests for the global live-refresh invalidation policy.
 * The React wiring (useQueryClient + stream subscriptions) is thin; the
 * behaviour worth pinning is WHICH query keys each event invalidates —
 * especially that a running/pending heartbeat invalidates NOTHING (no
 * refetch storm during a long scan) while a terminal event refreshes
 * the full scan-derived set.
 */
import { describe, expect, it } from "vitest";

import {
  scanEventInvalidations,
  scannerEventInvalidations,
} from "./useLiveDataRefresh";
import type { ScansStreamEvent } from "./useScansStreamEvents";
import type { ScannersStreamEvent } from "./useScannersStreamEvents";

function scanState(
  scan_status: "pending" | "running" | "completed" | "failed" | "cancelled",
): ScansStreamEvent {
  return {
    kind: "scan.state",
    scan_id: "s1",
    source_id: "src1",
    scan_status,
    source_status: "online",
    scanner_id: null,
    scanner_name: null,
    scan_type: "incremental",
    files_found: 0,
    current_path: null,
  };
}

const keys = (ks: ReturnType<typeof scanEventInvalidations>) =>
  ks.map((k) => (k as string[]).join("/"));

describe("scanEventInvalidations", () => {
  it("invalidates the full scan-derived set on a terminal scan.state", () => {
    for (const status of ["completed", "failed", "cancelled"] as const) {
      expect(keys(scanEventInvalidations(scanState(status))).sort()).toEqual(
        ["dashboard", "hosts", "scans", "sources"],
      );
    }
  });

  it("invalidates NOTHING on running/pending heartbeats (no refetch storm)", () => {
    expect(scanEventInvalidations(scanState("running"))).toEqual([]);
    expect(scanEventInvalidations(scanState("pending"))).toEqual([]);
  });

  it("resyncs the full set on a snapshot (reconnect may have missed terminals)", () => {
    const snap: ScansStreamEvent = { kind: "snapshot", scans: [] };
    expect(keys(scanEventInvalidations(snap)).sort()).toEqual(
      ["dashboard", "hosts", "scans", "sources"],
    );
  });

  it("invalidates only [sources] on source create/update/delete", () => {
    expect(keys(scanEventInvalidations({ kind: "source.updated", source_id: "x" }))).toEqual(["sources"]);
    expect(keys(scanEventInvalidations({ kind: "source.deleted", source_id: "x" }))).toEqual(["sources"]);
    expect(keys(scanEventInvalidations({
      kind: "source.created", source_id: "x", source_status: "online", name: "n", type: "smb",
    }))).toEqual(["sources"]);
  });

  it("invalidates only [hosts] on host.changed", () => {
    expect(keys(scanEventInvalidations({ kind: "host.changed", host_id: "h1" }))).toEqual(["hosts"]);
  });

  it("invalidates nothing on ping", () => {
    expect(scanEventInvalidations({ kind: "ping" })).toEqual([]);
  });
});

describe("scannerEventInvalidations", () => {
  it("invalidates [scanners] on a lifecycle event", () => {
    const ev: ScannersStreamEvent = { kind: "scanner.updated", scanner_id: "s1" };
    expect(keys(scannerEventInvalidations(ev))).toEqual(["scanners"]);
  });

  it("invalidates nothing on ping / error", () => {
    expect(scannerEventInvalidations({ kind: "ping" })).toEqual([]);
    expect(scannerEventInvalidations({ kind: "error", message: "x" })).toEqual([]);
  });
});
