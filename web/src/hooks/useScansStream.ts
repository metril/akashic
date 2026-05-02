/**
 * useScansStream — drop-in replacement for the polling-based
 * useActiveScans. Same return shape; data is kept in sync via the
 * /ws/scans push stream rather than a 2s refetch loop.
 */
import { useReducer, useState } from "react";

import type { Scan } from "../types";
import { useScansStreamEvents, type ScansStreamEvent } from "./useScansStreamEvents";

export interface ActiveScansResult {
  byScan: Record<string, Scan>;
  bySource: Record<string, Scan>;
  hasActive: boolean;
  status: "connecting" | "open" | "reconnecting";
}

interface State {
  byScan: Record<string, Scan>;
  bySource: Record<string, Scan>;
}

function recomputeBySource(byScan: Record<string, Scan>): Record<string, Scan> {
  const out: Record<string, Scan> = {};
  for (const s of Object.values(byScan)) {
    const cur = out[s.source_id];
    // Most-recent-by-started_at wins; pending (no started_at) loses
    // to running. Mirrors the v0.1.0 useActiveScans collapse rule.
    if (
      !cur ||
      (s.started_at && (!cur.started_at || s.started_at > cur.started_at))
    ) {
      out[s.source_id] = s;
    }
  }
  return out;
}

function reducer(state: State, event: ScansStreamEvent): State {
  switch (event.kind) {
    case "snapshot": {
      const byScan: Record<string, Scan> = {};
      for (const s of event.scans) {
        byScan[s.scan_id] = streamScanToModel(s);
      }
      return { byScan, bySource: recomputeBySource(byScan) };
    }
    case "scan.state": {
      const existing = state.byScan[event.scan_id];
      // Fast-path bail (v0.4.3): if no UI-visible field changed, return
      // the same state reference so useReducer skips the re-render.
      // Server-side coalescing already throttles heartbeats, but a
      // reattach or re-snapshot can still send an identical-shaped
      // event — no point churning the consumer for a no-op.
      if (existing
          && existing.status === event.scan_status
          && existing.files_found === (event.files_found ?? existing.files_found)
          && existing.current_path === (event.current_path ?? existing.current_path)) {
        return state;
      }
      const merged = streamEventToScan(event, existing);
      const byScan = { ...state.byScan, [event.scan_id]: merged };
      // Drop terminal scans from the live map after a short delay so
      // the UI shows the completion state momentarily, then collapses.
      // Implementation note: we keep them in the map; the consumer
      // (Sources page) displays based on source_status, not scan_status.
      return { byScan, bySource: recomputeBySource(byScan) };
    }
    case "source.deleted": {
      const byScan: Record<string, Scan> = {};
      for (const [id, s] of Object.entries(state.byScan)) {
        if (s.source_id !== event.source_id) byScan[id] = s;
      }
      return { byScan, bySource: recomputeBySource(byScan) };
    }
    case "source.created":
    case "ping":
    case "error":
      return state;
  }
}

function streamScanToModel(s: import("./useScansStreamEvents").SnapshotScan): Scan {
  return {
    id: s.scan_id,
    source_id: s.source_id,
    scan_type: s.scan_type,
    status: s.scan_status,
    files_found: s.files_found,
    files_new: 0,
    files_changed: 0,
    files_deleted: 0,
    started_at: s.started_at,
    completed_at: null,
    error_message: null,
    current_path: s.current_path,
  };
}

function streamEventToScan(
  e: Extract<ScansStreamEvent, { kind: "scan.state" }>,
  existing: Scan | undefined,
): Scan {
  // Preserve fields the event doesn't carry (per-scan progress
  // counters live on the per-scan WS, not the list-level one).
  const base: Scan = existing ?? {
    id: e.scan_id,
    source_id: e.source_id,
    scan_type: e.scan_type,
    status: e.scan_status,
    files_found: 0,
    files_new: 0,
    files_changed: 0,
    files_deleted: 0,
    started_at: null,
    completed_at: null,
    error_message: null,
    current_path: null,
  };
  return {
    ...base,
    status: e.scan_status,
    files_found: e.files_found ?? base.files_found,
    current_path: e.current_path ?? base.current_path,
  };
}

export function useScansStream(): ActiveScansResult {
  const [state, dispatch] = useReducer(reducer, {
    byScan: {},
    bySource: {},
  });
  const [status, setStatus] = useState<ActiveScansResult["status"]>("connecting");

  useScansStreamEvents((event) => {
    if (event.kind === "snapshot") setStatus("open");
    if (event.kind === "ping") setStatus("open");
    if (event.kind === "error") setStatus("reconnecting");
    dispatch(event);
  });

  return {
    byScan: state.byScan,
    bySource: state.bySource,
    hasActive: Object.keys(state.byScan).length > 0,
    status,
  };
}
