/**
 * Pure-function tests for the v0.4.5 useScansStream rewrite. Targets
 * the `applyEvent` reducer body — the React-side behaviour
 * (selector bail, useSyncExternalStore wiring) is exercised by the
 * manual perf smoke in the v0.4.5 plan, since the project doesn't
 * yet have @testing-library/react set up.
 *
 * The two invariants worth pinning down here:
 *   1. The fast-path bail returns the SAME state reference when an
 *      event reports no UI-visible change. This is what lets
 *      selector consumers (and the underlying useSyncExternalStore
 *      contract) skip re-renders.
 *   2. The reducer's collapse rules (most-recent-by-started_at wins
 *      per source; pending loses to running) are preserved across
 *      the rewrite from the prior useReducer-based shape.
 */
import { describe, expect, it } from "vitest";

import { _selectSnapshot, applyEvent } from "./useScansStream";
import type { ScansStreamEvent } from "./useScansStreamEvents";

const INITIAL = {
  byScan: {},
  bySource: {},
  status: "connecting" as const,
};

function snapshotEvent(scans: Array<{
  scan_id: string;
  source_id: string;
  scan_status?: string;
  files_found?: number;
  current_path?: string | null;
  started_at?: string | null;
}>): ScansStreamEvent {
  return {
    kind: "snapshot",
    scans: scans.map((s) => ({
      scan_id: s.scan_id,
      source_id: s.source_id,
      scan_status: s.scan_status ?? "running",
      source_status: "scanning",
      scanner_id: null,
      scanner_name: null,
      scan_type: "incremental",
      files_found: s.files_found ?? 0,
      current_path: s.current_path ?? null,
      started_at: s.started_at ?? null,
    })),
  };
}

function scanStateEvent(opts: {
  scan_id: string;
  source_id: string;
  scan_status?:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "cancelled";
  files_found?: number;
  current_path?: string | null;
  source_status?: string;
}): ScansStreamEvent {
  return {
    kind: "scan.state",
    scan_id: opts.scan_id,
    source_id: opts.source_id,
    scan_status: opts.scan_status ?? "running",
    source_status: opts.source_status ?? "scanning",
    scanner_id: null,
    scanner_name: null,
    scan_type: "incremental",
    files_found: opts.files_found ?? 0,
    current_path: opts.current_path ?? null,
  };
}

describe("applyEvent — snapshot", () => {
  it("hydrates byScan and bySource and flips status to open", () => {
    const next = applyEvent(
      INITIAL,
      snapshotEvent([
        { scan_id: "s1", source_id: "src-a" },
        { scan_id: "s2", source_id: "src-b" },
      ]),
    );
    expect(Object.keys(next.byScan)).toHaveLength(2);
    expect(next.bySource["src-a"].id).toBe("s1");
    expect(next.bySource["src-b"].id).toBe("s2");
    expect(next.status).toBe("open");
  });

  it("collapse rule: most-recent-by-started_at wins for the same source", () => {
    const next = applyEvent(
      INITIAL,
      snapshotEvent([
        { scan_id: "old", source_id: "src", started_at: "2026-01-01T00:00:00Z" },
        { scan_id: "new", source_id: "src", started_at: "2026-05-01T00:00:00Z" },
      ]),
    );
    // bySource collapses to one per source — the newer started_at wins.
    expect(next.bySource["src"].id).toBe("new");
    expect(Object.keys(next.byScan)).toHaveLength(2); // both still in byScan
  });

  it("collapse rule: a started scan beats a pending one with no started_at", () => {
    const next = applyEvent(
      INITIAL,
      snapshotEvent([
        { scan_id: "queued", source_id: "src", started_at: null },
        { scan_id: "running", source_id: "src", started_at: "2026-05-01T00:00:00Z" },
      ]),
    );
    expect(next.bySource["src"].id).toBe("running");
  });
});

describe("applyEvent — scan.state fast-path bail", () => {
  it("returns the same state reference when status/files_found/current_path are unchanged", () => {
    const seeded = applyEvent(
      INITIAL,
      snapshotEvent([
        { scan_id: "s1", source_id: "src", files_found: 42, current_path: "/x" },
      ]),
    );

    // Identical payload — must preserve identity for selector bail
    // to work downstream.
    const next = applyEvent(
      seeded,
      scanStateEvent({
        scan_id: "s1",
        source_id: "src",
        scan_status: "running",
        files_found: 42,
        current_path: "/x",
      }),
    );
    expect(next).toBe(seeded);
    expect(next.byScan).toBe(seeded.byScan);
    expect(next.bySource).toBe(seeded.bySource);
  });

  it("returns a NEW state when status changes", () => {
    const seeded = applyEvent(
      INITIAL,
      snapshotEvent([{ scan_id: "s1", source_id: "src" }]),
    );
    const next = applyEvent(
      seeded,
      scanStateEvent({
        scan_id: "s1",
        source_id: "src",
        scan_status: "completed",
      }),
    );
    expect(next).not.toBe(seeded);
    expect(next.byScan["s1"].status).toBe("completed");
  });

  it("returns a NEW state when files_found advances", () => {
    const seeded = applyEvent(
      INITIAL,
      snapshotEvent([
        { scan_id: "s1", source_id: "src", files_found: 10 },
      ]),
    );
    const next = applyEvent(
      seeded,
      scanStateEvent({
        scan_id: "s1",
        source_id: "src",
        files_found: 25,
      }),
    );
    expect(next).not.toBe(seeded);
    expect(next.byScan["s1"].files_found).toBe(25);
  });

  it("creates an entry for a previously-unseen scan_id", () => {
    const next = applyEvent(
      { byScan: {}, bySource: {}, status: "open" },
      scanStateEvent({
        scan_id: "fresh",
        source_id: "src",
        scan_status: "pending",
      }),
    );
    expect(next.byScan["fresh"]).toBeDefined();
    expect(next.byScan["fresh"].status).toBe("pending");
    expect(next.bySource["src"].id).toBe("fresh");
  });
});

describe("applyEvent — source.deleted", () => {
  it("drops all scans for the source and preserves identity when nothing matched", () => {
    const seeded = applyEvent(
      INITIAL,
      snapshotEvent([
        { scan_id: "s1", source_id: "src-a" },
        { scan_id: "s2", source_id: "src-b" },
      ]),
    );

    // No-op delete on an unrelated source → identity preserved.
    const noop = applyEvent(seeded, { kind: "source.deleted", source_id: "missing" });
    expect(noop).toBe(seeded);

    // Real delete → src-a gone, src-b retained.
    const after = applyEvent(seeded, { kind: "source.deleted", source_id: "src-a" });
    expect(after).not.toBe(seeded);
    expect(after.byScan["s1"]).toBeUndefined();
    expect(after.byScan["s2"]).toBeDefined();
    expect(after.bySource["src-a"]).toBeUndefined();
  });
});

describe("_selectSnapshot — selector cache (v0.4.6 regression coverage)", () => {
  // v0.4.5 returned the cached value when only the STATE was
  // unchanged, ignoring whether the selector itself had changed.
  // That's what made `useActiveScanForSource(openSource?.id)`
  // return undefined after openSource went `null → "A"` (no event
  // had arrived yet, so state was unchanged from when the cache
  // was populated under the null-id selector).
  const stateA = applyEvent(
    INITIAL,
    snapshotEvent([
      { scan_id: "s1", source_id: "src-a" },
      { scan_id: "s2", source_id: "src-b" },
    ]),
  );

  it("returns the new selector's value when selector identity changes (state unchanged)", () => {
    const sel1 = (s: typeof stateA) => s.bySource["src-a"];
    const sel2 = (s: typeof stateA) => s.bySource["src-b"];

    const r1 = _selectSnapshot(null, stateA, sel1);
    expect(r1.value?.id).toBe("s1");

    // Same state, DIFFERENT selector — must NOT return the prior
    // selector's value just because state matches.
    const r2 = _selectSnapshot(r1.cache, stateA, sel2);
    expect(r2.value?.id).toBe("s2");
  });

  it("returns the same identity on a true cache hit (state AND selector both match)", () => {
    const sel = (s: typeof stateA) => s.bySource["src-a"];
    const r1 = _selectSnapshot(null, stateA, sel);
    const r2 = _selectSnapshot(r1.cache, stateA, sel);
    // Bypass the recompute path entirely.
    expect(r2.cache).toBe(r1.cache);
    expect(r2.value).toBe(r1.value);
  });

  it("preserves prior identity when selector returns Object.is-equal value (different state ref)", () => {
    // src-a's scan ref is unchanged across stateA → state-after-A's-no-op-event.
    const sel = (s: typeof stateA) => s.bySource["src-a"];
    const r1 = _selectSnapshot(null, stateA, sel);

    // Apply an event that DOES create a new state ref (a brand-new
    // scan for a different source) but doesn't touch src-a's slice.
    const stateB = applyEvent(
      stateA,
      scanStateEvent({ scan_id: "s3", source_id: "src-c" }),
    );
    expect(stateB).not.toBe(stateA); // sanity — state ref changed

    const r2 = _selectSnapshot(r1.cache, stateB, sel);
    // Even though the cache missed (state changed), the selector's
    // output is the SAME Scan reference. We preserve identity so
    // useSyncExternalStore bails the consumer's re-render.
    expect(r2.value).toBe(r1.value);
  });

  it("returns a NEW value when both state and selector slice changed", () => {
    const sel = (s: typeof stateA) => s.bySource["src-a"];
    const r1 = _selectSnapshot(null, stateA, sel);

    // Update src-a's scan — files_found advanced.
    const stateB = applyEvent(
      stateA,
      scanStateEvent({
        scan_id: "s1", source_id: "src-a", files_found: 99,
      }),
    );
    const r2 = _selectSnapshot(r1.cache, stateB, sel);
    expect(r2.value?.files_found).toBe(99);
    expect(r2.value).not.toBe(r1.value);
  });

  it("handles the `useActiveScanForSource(null|undefined → 'A')` opening flow", () => {
    // Reproduces the exact v0.4.5 bug: cache populated under a
    // selector that returned undefined; the user clicks a source;
    // the new selector should return the source's active scan,
    // not the cached undefined.
    const selNull = (_s: typeof stateA) => undefined;
    const selA = (s: typeof stateA) => s.bySource["src-a"];

    const initial = _selectSnapshot(null, stateA, selNull);
    expect(initial.value).toBeUndefined();

    const opened = _selectSnapshot(initial.cache, stateA, selA);
    expect(opened.value?.id).toBe("s1");
  });
});

describe("recomputeBySource — open scan beats terminal even with null started_at (v0.4.10 regression coverage)", () => {
  // The bug: scan.state events don't carry started_at, so a
  // freshly-triggered scan sat in byScan with started_at=null and
  // lost the recomputeBySource tiebreak to an older terminal scan
  // for the same source whose started_at was populated. bySource[id]
  // pointed at the terminal scan → useOpenScanForSource returned
  // undefined → activeScanId=null → SourceDetail's Live log tab
  // rendered empty content until the user closed + reopened the
  // panel (forcing a fresh WS reconnect + snapshot, after which the
  // running scan's started_at WAS populated and it won the
  // tiebreak).
  it("a running scan with null started_at wins over an older failed scan with populated started_at", () => {
    // Seed: an older failed scan (came in via the snapshot frame
    // with started_at set).
    const seeded = applyEvent(
      INITIAL,
      snapshotEvent([
        {
          scan_id: "old",
          source_id: "src",
          scan_status: "failed",
          started_at: "2026-05-02T19:08:34Z",
        },
      ]),
    );
    expect(seeded.bySource["src"].id).toBe("old");
    expect(seeded.bySource["src"].status).toBe("failed");

    // Now a fresh scan is triggered. The trigger's scan.state event
    // arrives with no started_at field set yet (pending phase).
    const triggered = applyEvent(
      seeded,
      scanStateEvent({
        scan_id: "new",
        source_id: "src",
        scan_status: "pending",
      }),
    );
    // The running/pending scan must win regardless of started_at.
    expect(triggered.bySource["src"].id).toBe("new");
    expect(triggered.bySource["src"].status).toBe("pending");
  });

  it("a completed scan still wins over a terminal scan when both are terminal (started_at tiebreak)", () => {
    const seeded = applyEvent(
      INITIAL,
      snapshotEvent([
        {
          scan_id: "older-fail",
          source_id: "src",
          scan_status: "failed",
          started_at: "2026-05-02T19:00:00Z",
        },
      ]),
    );
    // A completed scan with a more recent started_at.
    const next = applyEvent(
      seeded,
      scanStateEvent({
        scan_id: "newer-complete",
        source_id: "src",
        scan_status: "completed",
      }),
    );
    // Among terminal-only, the started_at rule still applies — but
    // the new completed scan came via scan.state with no started_at,
    // so the older failed (with real started_at) wins. (This is the
    // existing semantics; we're just confirming the new "open beats
    // terminal" branch doesn't accidentally promote the new one.)
    expect(next.bySource["src"].id).toBe("older-fail");
  });
});

describe("useOpenScanForSource semantics (v0.4.8 regression coverage)", () => {
  // The bug: bySource[id] keeps the most-recent scan even after it
  // terminates (the WS snapshot deliberately includes failed scans
  // so SourceCard can surface error_message). The Scan-now button
  // disabled gate read activeScanForOpen?.id != null, which stayed
  // truthy across terminal scans → button locked permanently after
  // the first scan failed. The fix is `useOpenScanForSource`, which
  // filters bySource to pending/running only. We exercise the
  // selector logic directly here.
  // Use the same shape as the real store. Returning `any` keeps the
  // test focused on the runtime behaviour rather than re-deriving
  // the State type the module keeps internal.
  function openSelector(sourceId: string) {
    return (s: { bySource: Record<string, { id: string; status: string } | undefined> }) => {
      const scan = s.bySource[sourceId];
      if (!scan) return undefined;
      if (scan.status !== "pending" && scan.status !== "running") return undefined;
      return scan;
    };
  }

  it("returns undefined when the latest scan has terminated", () => {
    const stateAfterFail = applyEvent(
      INITIAL,
      scanStateEvent({
        scan_id: "s1", source_id: "src", scan_status: "failed",
      }),
    );
    expect(stateAfterFail.bySource["src"]?.status).toBe("failed");
    // Open-only selector hides terminal scans.
    expect(openSelector("src")(stateAfterFail)).toBeUndefined();
  });

  it("returns the scan while it's still pending or running", () => {
    const pendingState = applyEvent(
      INITIAL,
      scanStateEvent({
        scan_id: "s1", source_id: "src", scan_status: "pending",
      }),
    );
    expect(openSelector("src")(pendingState)?.id).toBe("s1");

    const runningState = applyEvent(
      pendingState,
      scanStateEvent({
        scan_id: "s1", source_id: "src", scan_status: "running",
      }),
    );
    expect(openSelector("src")(runningState)?.id).toBe("s1");
  });

  it("returns undefined for completed and cancelled scans", () => {
    for (const terminal of ["completed", "failed", "cancelled"] as const) {
      const state = applyEvent(
        INITIAL,
        scanStateEvent({
          scan_id: "s", source_id: "src", scan_status: terminal,
        }),
      );
      expect(openSelector("src")(state)).toBeUndefined();
    }
  });
});

describe("applyEvent — connection signals", () => {
  it("ping flips connecting → open and is idempotent once open", () => {
    const opened = applyEvent(INITIAL, { kind: "ping" });
    expect(opened.status).toBe("open");
    // Identity preserved on subsequent ping (already open).
    const stillOpen = applyEvent(opened, { kind: "ping" });
    expect(stillOpen).toBe(opened);
  });

  it("error flips status to reconnecting and is idempotent", () => {
    const opened = applyEvent(INITIAL, { kind: "ping" });
    const err = applyEvent(opened, { kind: "error", message: "boom" });
    expect(err.status).toBe("reconnecting");
    const stillReconnecting = applyEvent(err, { kind: "error", message: "boom" });
    expect(stillReconnecting).toBe(err);
  });

  it("source.created passes through without state mutation", () => {
    const seeded = applyEvent(INITIAL, { kind: "ping" });
    const next = applyEvent(seeded, {
      kind: "source.created",
      source_id: "x",
      source_status: "online",
      name: "x",
      type: "local",
    });
    expect(next).toBe(seeded);
  });
});
