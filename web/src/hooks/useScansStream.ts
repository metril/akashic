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

const TERMINAL_SCAN_STATES = new Set(["completed", "failed", "cancelled"]);

function isOpenScan(s: Scan): boolean {
  return !TERMINAL_SCAN_STATES.has(s.status);
}

function recomputeBySource(byScan: Record<string, Scan>): Record<string, Scan> {
  const out: Record<string, Scan> = {};
  for (const s of Object.values(byScan)) {
    const cur = out[s.source_id];
    if (!cur) {
      out[s.source_id] = s;
      continue;
    }
    // v0.4.10 — preference order:
    //   1. Open (pending/running) ALWAYS wins over terminal. Without
    //      this, a freshly-triggered scan with started_at=null in the
    //      singleton store (the WS scan.state event payload doesn't
    //      carry started_at — only the snapshot frame does) loses to
    //      an older completed/failed scan that's still in byScan with
    //      a real started_at. That hid the running scan from
    //      useOpenScanForSource → activeScanId stayed null →
    //      SourceDetail's Live log tab content was empty until the
    //      user closed and reopened the panel (which forced a fresh
    //      WS reconnect + snapshot, after which the running scan had
    //      a populated started_at and won the comparison).
    //   2. Among scans of the same kind (both open or both closed),
    //      most-recent-by-started_at wins; null started_at loses to
    //      a real timestamp.
    const sOpen = isOpenScan(s);
    const curOpen = isOpenScan(cur);
    if (sOpen && !curOpen) {
      out[s.source_id] = s;
      continue;
    }
    if (!sOpen && curOpen) {
      continue;
    }
    if (s.started_at && (!cur.started_at || s.started_at > cur.started_at)) {
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
    // v0.4.10 — merge started_at when the event provides it. The
    // backend started sending it on lease/heartbeat/complete so the
    // singleton store carries the real timestamp end-to-end (used
    // for ETA in buildProgressLine + tiebreaker in
    // recomputeBySource).
    started_at: e.started_at ?? base.started_at,
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
    case "source.updated":
    case "host.changed":
      // Not material to the per-scan store; handled by useLiveDataRefresh
      // (cache invalidation) instead.
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
  // v0.4.6: do NOT reset _state. Keep the last-known maps across
  // brief unmount cycles (React StrictMode in dev, rapid page
  // navigation, or a single React commit that drops the last
  // subscriber and immediately gains a new one). The next snapshot
  // from the server overwrites on reconnect anyway, so wiping was
  // pure downside — it caused a flicker through "connecting" + empty
  // bySource between unmount and the next snapshot frame.
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
 * when the selected slice is Object.is-equal to the previous
 * selection.
 *
 * Cache invalidation key (v0.4.6): BOTH `state` reference AND
 * `selector` reference. Tracking only state was wrong — when a
 * caller passes `s => s.bySource[id]` and `id` changes (e.g.,
 * `useActiveScanForSource(openSource?.id)` going `undefined → "A"`
 * after a card click), the closure changes but state has not yet,
 * and we'd return the stale prior value (often `undefined`). That
 * left the panel showing "Scan now" when a scan was in flight,
 * hid the Live log tab, and made the page look frozen until the
 * next WS event happened to invalidate the cache.
 *
 * Inline lambdas in callers (the helper hooks below) mean the
 * selector identity changes every render. That makes the cache
 * miss on every render — which is fine, because the work is one
 * O(1) selector call plus one Object.is comparison; the bail
 * happens via Object.is when the slice is reference-equal to the
 * previous, preserving the identity React uses to skip re-renders.
 */
interface SelectorCache<T> {
  state: State;
  selector: (s: State) => T;
  value: T;
}

/**
 * Pure cache-lookup helper. Exported for tests; not for consumer
 * use. Returns BOTH the value (for the caller to return) AND the
 * cache entry to write back, so the React hook's ref update stays
 * a single line.
 *
 * Cache hits when state AND selector identity both match. On miss,
 * runs the selector; if its output is Object.is-equal to the
 * previously-cached value, preserves that prior reference so
 * downstream identity comparisons (React.memo, useSyncExternalStore
 * bail) stay valid.
 */
export function _selectSnapshot<T>(
  cached: SelectorCache<T> | null,
  current: State,
  selector: (s: State) => T,
): { value: T; cache: SelectorCache<T> } {
  if (cached && cached.state === current && cached.selector === selector) {
    return { value: cached.value, cache: cached };
  }
  const next = selector(current);
  if (cached && Object.is(cached.value, next)) {
    const updated: SelectorCache<T> = { state: current, selector, value: cached.value };
    return { value: cached.value, cache: updated };
  }
  const fresh: SelectorCache<T> = { state: current, selector, value: next };
  return { value: next, cache: fresh };
}

export function useScansStreamSelect<T>(selector: (s: State) => T): T {
  const selectorRef = useRef(selector);
  selectorRef.current = selector;
  const cacheRef = useRef<SelectorCache<T> | null>(null);

  const getSelectedSnapshot = useCallback((): T => {
    const result = _selectSnapshot(cacheRef.current, _state, selectorRef.current);
    cacheRef.current = result.cache;
    return result.value;
  }, []);

  return useSyncExternalStore(subscribe, getSelectedSnapshot, getSelectedSnapshot);
}

/**
 * The latest scan for a given source — pending, running, OR
 * recently-terminated (failed/completed/cancelled). Returns
 * whatever bySource has, which is the most-recent-by-started_at.
 *
 * Use this for surfacing per-scan metadata like `error_message`
 * on a Failed source card. Do NOT use this to gate "is there an
 * in-flight scan" UI — terminal scans linger here until the next
 * snapshot, which would leave Scan-now buttons disabled forever.
 * Use useOpenScanForSource for that.
 */
export function useActiveScanForSource(
  sourceId: string | null | undefined,
): Scan | undefined {
  return useScansStreamSelect((s) =>
    sourceId ? s.bySource[sourceId] : undefined,
  );
}

/**
 * The OPEN scan for a given source — only pending or running.
 * Returns undefined when the latest scan has terminated, even if
 * it's still in bySource.
 *
 * v0.4.8: split out from useActiveScanForSource because the
 * SourceDetail panel was disabling its "Scan now" button forever
 * after the first scan terminated — bySource[id] still held the
 * failed scan (the WS snapshot deliberately includes failed scans
 * so SourceCard can surface error_message), and any non-undefined
 * activeScan was being treated as "scan in flight". Result: users
 * couldn't trigger a follow-up scan, the button looked permanently
 * stuck on "Queued…", and the page felt frozen.
 */
export function useOpenScanForSource(
  sourceId: string | null | undefined,
): Scan | undefined {
  return useScansStreamSelect((s) => {
    if (!sourceId) return undefined;
    const scan = s.bySource[sourceId];
    if (!scan) return undefined;
    if (scan.status !== "pending" && scan.status !== "running") {
      return undefined;
    }
    return scan;
  });
}

/**
 * One scan by its id, as tracked by the list-level stream. Unlike the
 * per-scan WebSocket snapshot (captured once at connect and never
 * corrected), `byScan` receives live `scan.state` events including the
 * terminal transition — so a consumer reading status from here always
 * sees the *current* status. Returns undefined when the id isn't in
 * the live map (e.g. an old scan past the snapshot window).
 */
export function useScanById(
  scanId: string | null | undefined,
): Scan | undefined {
  return useScansStreamSelect((s) =>
    scanId ? s.byScan[scanId] : undefined,
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
