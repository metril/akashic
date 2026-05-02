/**
 * useScansStream — module-singleton WS state store with selector hooks.
 *
 * v0.4.5 rewrite. Previously each consumer (Sources page + every
 * visible SourceCard + the open SourceDetail panel) called
 * useReducer in its own mount. A single WS scan.state frame fanned
 * out to ~10 listeners, each ran the reducer (allocating its own
 * byScan + bySource maps), each re-rendered unconditionally. That
 * O(N consumers) cost per event is what made the source panel feel
 * sluggish during active scans.
 *
 * After this rewrite:
 *   - State lives in ONE module-level cell, updated by ONE dispatch
 *     callback wired to subscribeRawScansEvents.
 *   - Consumers use useScansStreamSelect(s => slice) which goes
 *     through useSyncExternalStore. React bails the re-render when
 *     the selected slice is Object.is-equal to the prior one.
 *   - Named helpers (useActiveScanForSource, useHasActiveScans,
 *     useScansStreamStatus) wrap the common selector lambdas so call
 *     sites stay self-documenting.
 *
 * The legacy `useScansStream()` shape (returning the full
 * { byScan, bySource, hasActive, status }) is preserved for backward
 * compatibility — internally it's now useScansStreamFull(), which
 * still re-renders on every state change. Prefer the named helpers
 * for any code path on the render hot path.
 */
import { useCallback, useRef, useSyncExternalStore } from "react";

import type { Scan } from "../types";
import {
  type ScansStreamEvent,
  type SnapshotScan,
  subscribeRawScansEvents,
} from "./useScansStreamEvents";

interface State {
  byScan: Record<string, Scan>;
  bySource: Record<string, Scan>;
  status: "connecting" | "open" | "reconnecting";
}

export interface ActiveScansResult {
  byScan: Record<string, Scan>;
  bySource: Record<string, Scan>;
  hasActive: boolean;
  status: State["status"];
}

const INITIAL_STATE: State = {
  byScan: {},
  bySource: {},
  status: "connecting",
};

let _state: State = INITIAL_STATE;
const _listeners = new Set<() => void>();

let _wired = false;
let _unwire: (() => void) | null = null;

function notify(): void {
  for (const l of _listeners) l();
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

function streamScanToModel(s: SnapshotScan): Scan {
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

/**
 * Pure reducer body — exposed for tests. Returns the SAME state
 * reference when nothing changed, so useSyncExternalStore can bail.
 */
export function applyEvent(state: State, event: ScansStreamEvent): State {
  switch (event.kind) {
    case "snapshot": {
      const byScan: Record<string, Scan> = {};
      for (const s of event.scans) {
        byScan[s.scan_id] = streamScanToModel(s);
      }
      return { ...state, byScan, bySource: recomputeBySource(byScan), status: "open" };
    }
    case "scan.state": {
      const existing = state.byScan[event.scan_id];
      // Fast-path bail: identical event payload → preserve identity
      // so selector consumers don't re-render. Server-side coalescing
      // already throttles heartbeats, but a reattach or re-snapshot
      // can still send an identical-shaped event.
      if (
        existing &&
        existing.status === event.scan_status &&
        existing.files_found === (event.files_found ?? existing.files_found) &&
        existing.current_path === (event.current_path ?? existing.current_path)
      ) {
        return state;
      }
      const merged = streamEventToScan(event, existing);
      const byScan = { ...state.byScan, [event.scan_id]: merged };
      return { ...state, byScan, bySource: recomputeBySource(byScan) };
    }
    case "source.deleted": {
      let dropped = false;
      const byScan: Record<string, Scan> = {};
      for (const [id, s] of Object.entries(state.byScan)) {
        if (s.source_id !== event.source_id) byScan[id] = s;
        else dropped = true;
      }
      if (!dropped) return state;
      return { ...state, byScan, bySource: recomputeBySource(byScan) };
    }
    case "source.created":
      return state;
    case "ping":
      // Pings aren't material, but they ARE proof of liveness — flip
      // the connection status to "open" if we'd been showing
      // "connecting" or "reconnecting".
      if (state.status === "open") return state;
      return { ...state, status: "open" };
    case "error":
      if (state.status === "reconnecting") return state;
      return { ...state, status: "reconnecting" };
  }
}

function dispatch(event: ScansStreamEvent): void {
  // The events module already dispatches a fresh `snapshot` frame
  // on (re)connect; treating snapshot as the "open" signal keeps
  // the status in sync without needing a separate channel.
  if (event.kind === "snapshot" && _state.status !== "open") {
    // applyEvent will set status: "open"; let it through.
  }
  const next = applyEvent(_state, event);
  if (next === _state) return; // fast-path bail at the store level
  _state = next;
  notify();
}

function ensureWired(): void {
  if (_wired) return;
  _wired = true;
  _unwire = subscribeRawScansEvents(dispatch);
}

function unwireIfIdle(): void {
  if (_listeners.size > 0) return;
  if (_unwire) {
    _unwire();
    _unwire = null;
  }
  _wired = false;
  // Reset to initial so a future remount sees "connecting" until the
  // next snapshot arrives, not stale data from before the unmount.
  _state = INITIAL_STATE;
}

function subscribe(listener: () => void): () => void {
  ensureWired();
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
    unwireIfIdle();
  };
}

function getSnapshot(): State {
  return _state;
}

/**
 * Subscribe to the FULL state snapshot. Re-renders on every state
 * change. Use only when you legitimately need the whole map (e.g.
 * legacy callers); prefer useScansStreamSelect or a named helper.
 */
export function useScansStreamFull(): ActiveScansResult {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return {
    byScan: state.byScan,
    bySource: state.bySource,
    hasActive: Object.keys(state.byScan).length > 0,
    status: state.status,
  };
}

/**
 * Subscribe to a slice of the state. The hook keeps a per-component
 * cache of the last selected value so React bails the re-render
 * when (a) the store didn't change OR (b) the store changed but the
 * selected slice is Object.is-equal to the previous selection.
 *
 * Selector identity may change between renders (inline lambdas are
 * fine); we re-read it via a ref so the cache stays valid.
 */
export function useScansStreamSelect<T>(selector: (s: State) => T): T {
  const selectorRef = useRef(selector);
  selectorRef.current = selector;

  // Cache the (state ref, selected value) pair so subsequent
  // getSnapshot calls return the SAME T reference until either the
  // store mutates OR the selector starts returning a different
  // value. useSyncExternalStore relies on Object.is between
  // consecutive getSnapshot returns to decide whether to re-render.
  const cacheRef = useRef<{ state: State; value: T } | null>(null);

  const getSelectedSnapshot = useCallback((): T => {
    const current = _state;
    const cached = cacheRef.current;
    if (cached && cached.state === current) {
      return cached.value;
    }
    const next = selectorRef.current(current);
    if (cached && Object.is(cached.value, next)) {
      // Slice is reference-equal to the prior — preserve identity so
      // useSyncExternalStore bails the re-render.
      cacheRef.current = { state: current, value: cached.value };
      return cached.value;
    }
    cacheRef.current = { state: current, value: next };
    return next;
  }, []);

  return useSyncExternalStore(subscribe, getSelectedSnapshot, getSelectedSnapshot);
}

/**
 * The active (pending or running) scan for a given source, or
 * undefined. Re-renders only when THIS source's scan changes — a
 * scan.state event for a different source flips no listeners on
 * this consumer.
 */
export function useActiveScanForSource(
  sourceId: string | null | undefined,
): Scan | undefined {
  return useScansStreamSelect((s) =>
    sourceId ? s.bySource[sourceId] : undefined,
  );
}

/** True when any scan is in the live map. Used by the Sources
 *  page's "no scanner" banner gate. */
export function useHasActiveScans(): boolean {
  return useScansStreamSelect((s) => Object.keys(s.byScan).length > 0);
}

/** WebSocket connection status — "connecting" | "open" | "reconnecting". */
export function useScansStreamStatus(): State["status"] {
  return useScansStreamSelect((s) => s.status);
}

/**
 * Backwards-compatible wrapper. Same return shape as the v0.4.4
 * hook so existing callers that read the full bySource map don't
 * have to migrate at the same time. Internally it's a full-state
 * subscription — re-renders on every state change. Prefer the named
 * helpers above for new code.
 */
export function useScansStream(): ActiveScansResult {
  return useScansStreamFull();
}

// ─────────────────────────────────────────────────────────────────
// Test helpers (only used by useScansStream.test.tsx). Not exported
// from a barrel; consumers shouldn't reach for these.
// ─────────────────────────────────────────────────────────────────

export function _resetForTest(): void {
  _state = INITIAL_STATE;
  _listeners.clear();
  if (_unwire) {
    _unwire();
    _unwire = null;
  }
  _wired = false;
}

export function _dispatchForTest(event: ScansStreamEvent): void {
  // Test entry point that bypasses the real WS subscription. Mirrors
  // dispatch() but doesn't require ensureWired().
  const next = applyEvent(_state, event);
  if (next === _state) return;
  _state = next;
  notify();
}
