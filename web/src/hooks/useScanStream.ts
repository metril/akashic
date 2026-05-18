import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "../api/client";
import { isStreamStale } from "../components/scans/scanLog";
import type {
  ScanLogLine,
  ScanProgressEvent,
  ScanSnapshot,
  ScanWsEvent,
} from "../types";

// v0.33.0 — the viewer loads the *entire* persisted log so its client-side
// level / search / scanner filters operate on the whole history, not just a
// tail. History is paged forward with a (ts, id) keyset; 2000 matches the
// GET /api/scans/{id}/log page cap. There is no in-memory line cap any more
// — ScanLogPanel virtualizes the render, so an unbounded list is cheap.
const HISTORY_PAGE_SIZE = 2000;

// Half-open-socket watchdog. The server pings every 30 s; if no frame
// (incl. ping) has arrived for STALE_MS the socket is dead even though the
// browser never fired `close`/`error` — force a reconnect. 45 s is 1.5 ping
// intervals of grace. WATCHDOG_MS is how often we check.
const STALE_MS = 45_000;
const WATCHDOG_MS = 15_000;

export interface ScanStreamState {
  snapshot: ScanSnapshot | null;
  progress: ScanProgressEvent | null;
  // The full log: persisted history (page-loaded on mount) + live WS lines.
  lines: ScanLogLine[];
  // "connecting" = first connect; "reconnecting" = re-establishing after a
  // drop (incl. a watchdog-forced reconnect).
  status: "connecting" | "open" | "reconnecting" | "closed" | "error";
  // True while the initial full-history page-loop is still running.
  historyLoading: boolean;
}

const initialState: ScanStreamState = {
  snapshot: null,
  progress: null,
  lines: [],
  status: "connecting",
  historyLoading: false,
};

// mergeLines — dedupe `incoming` against `existing` by id, then return one
// (ts, id)-ordered array. Used for the history page-loop and the reconnect
// backfill, where fresh rows can sort anywhere relative to what's loaded.
// Exported for unit tests.
export function mergeLines(
  existing: ScanLogLine[],
  incoming: ScanLogLine[],
): ScanLogLine[] {
  const seen = new Set(existing.map((l) => l.id));
  const fresh = incoming.filter((l) => !seen.has(l.id));
  if (fresh.length === 0) return existing;
  // Decorate with a numeric ts so the comparator doesn't re-parse the ISO
  // string O(n log n) times — fractional seconds make a lexical string
  // compare unreliable, so a numeric key is also the correct one.
  const decorated = [...existing, ...fresh].map((l) => ({
    l,
    t: Date.parse(l.ts),
  }));
  decorated.sort(
    (a, b) =>
      a.t - b.t ||
      (a.l.id < b.l.id ? -1 : a.l.id > b.l.id ? 1 : 0),
  );
  return decorated.map((d) => d.l);
}

// appendLines — dedupe `incoming` by id and append in arrival order. Used
// for live WS log/stderr events, which arrive newest-last. Exported for
// unit tests.
export function appendLines(
  existing: ScanLogLine[],
  incoming: ScanLogLine[],
): ScanLogLine[] {
  const seen = new Set(existing.map((l) => l.id));
  const fresh = incoming.filter((l) => !seen.has(l.id));
  return fresh.length === 0 ? existing : [...existing, ...fresh];
}

// fetchLogPage — one keyset page of GET /api/scans/{id}/log. `cursor` is
// the last row of the previous page (null for the first page). `(ts, id)`
// is exact: a pure-ts cursor would drop lines when a batch shares a ts.
async function fetchLogPage(
  scanId: string,
  token: string,
  cursor: { ts: string; id: string } | null,
): Promise<ScanLogLine[]> {
  let url = `/api/scans/${scanId}/log?kind=all&limit=${HISTORY_PAGE_SIZE}`;
  if (cursor) {
    url +=
      `&since=${encodeURIComponent(cursor.ts)}` +
      `&after_id=${encodeURIComponent(cursor.id)}`;
  }
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`log page ${resp.status}`);
  return (await resp.json()) as ScanLogLine[];
}

/**
 * useScanStream — exposes a scan's full log plus live progress.
 *
 * On mount it page-loops GET /api/scans/{id}/log to load the *entire*
 * persisted history, then (for a still-running scan) a WebSocket to
 * /ws/scans/{id} streams new lines on top. Reconnects with exponential
 * backoff; backfills the gap via the same keyset GET.
 *
 * Set `enabled=false` to suspend everything (panel closed / unmounted).
 */
export function useScanStream(scanId: string | null, enabled: boolean = true) {
  const [state, setState] = useState<ScanStreamState>(initialState);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const attemptRef = useRef(0);
  // Epoch-ms of the last frame received (any kind, incl. `ping`). The
  // watchdog reads this to detect a half-open socket.
  const lastActivityRef = useRef<number | null>(null);
  // Mounted-flag — a rAF callback or an awaited fetch can resolve after
  // unmount; guards bail before touching state.
  const mountedRef = useRef(true);

  // Mirror of state.lines so connect()'s onopen can read the newest row as
  // the reconnect-backfill cursor without re-creating the callback.
  const linesRef = useRef<ScanLogLine[]>([]);
  useEffect(() => {
    linesRef.current = state.lines;
  }, [state.lines]);

  // Generation token for the history page-loop: bumping it makes an
  // in-flight loop bail when scanId/enabled changes or the hook unmounts.
  const historyGenRef = useRef(0);

  // Inbound live-event coalescing buffer. A burst of WS frames becomes one
  // setState per animation frame rather than one per message.
  const pendingRef = useRef<{
    progress: ScanProgressEvent | null;
    snapshot: ScanSnapshot | null;
    appendLines: ScanLogLine[];
  }>({ progress: null, snapshot: null, appendLines: [] });
  const flushScheduledRef = useRef(false);

  const scheduleFlush = useCallback(() => {
    if (flushScheduledRef.current) return;
    flushScheduledRef.current = true;
    requestAnimationFrame(() => {
      flushScheduledRef.current = false;
      if (!mountedRef.current) return;
      const pending = pendingRef.current;
      pendingRef.current = { progress: null, snapshot: null, appendLines: [] };
      if (
        !pending.progress &&
        !pending.snapshot &&
        pending.appendLines.length === 0
      ) {
        return;
      }
      setState((s) => ({
        ...s,
        // v0.33.0 — the WS snapshot contributes metadata only. Its
        // `recent_lines` (last 100) is deliberately ignored: the history
        // page-loop owns `lines`, and letting the snapshot replace the
        // buffer would clobber the full log we loaded.
        snapshot: pending.snapshot ?? s.snapshot,
        progress: pending.progress ?? s.progress,
        lines:
          pending.appendLines.length > 0
            ? appendLines(s.lines, pending.appendLines)
            : s.lines,
      }));
    });
  }, []);

  // `enabled` mirrored into a ref so the WS `onclose` handler reads the
  // current value rather than the value captured at connect() time.
  const enabledRef = useRef(enabled);
  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  const connect = useCallback(() => {
    if (!scanId || !enabled) return;
    const token = getToken();
    if (!token) {
      setState((s) => ({ ...s, status: "error" }));
      return;
    }

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/scans/${scanId}?token=${encodeURIComponent(token)}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    setState((s) => ({
      ...s,
      status: attemptRef.current > 0 ? "reconnecting" : "connecting",
    }));

    ws.onopen = async () => {
      attemptRef.current = 0;
      lastActivityRef.current = Date.now();
      setState((s) => ({ ...s, status: "open" }));
      // Backfill anything that landed between drop and reconnect, keyed off
      // the newest line currently held (exact (ts, id) keyset).
      const newest = linesRef.current[linesRef.current.length - 1];
      if (newest) {
        try {
          const lines = await fetchLogPage(scanId, token, {
            ts: newest.ts,
            id: newest.id,
          });
          if (lines.length && mountedRef.current) {
            setState((s) => ({ ...s, lines: mergeLines(s.lines, lines) }));
          }
        } catch {
          // Backfill failure is non-fatal — the live stream takes over.
        }
      }
    };

    ws.onmessage = (ev) => {
      // Mark the socket alive on EVERY frame — a `ping` is proof of
      // liveness, which is what the half-open watchdog needs.
      lastActivityRef.current = Date.now();
      let event: ScanWsEvent;
      try {
        event = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (event.kind === "ping") return;
      if (event.kind === "snapshot") {
        pendingRef.current.snapshot = event;
      } else if (event.kind === "progress") {
        pendingRef.current.progress = event;
      } else if (event.kind === "log" || event.kind === "stderr") {
        if (event.lines && event.lines.length > 0) {
          pendingRef.current.appendLines.push(...event.lines);
        }
      } else {
        return;
      }
      scheduleFlush();
    };

    ws.onclose = () => {
      wsRef.current = null;
      if (!enabledRef.current) {
        setState((s) => ({ ...s, status: "closed" }));
        return;
      }
      attemptRef.current += 1;
      const delay = Math.min(1000 * 2 ** (attemptRef.current - 1), 10_000);
      setState((s) => ({ ...s, status: "reconnecting" }));
      reconnectTimer.current = window.setTimeout(connect, delay);
    };

    ws.onerror = () => {
      setState((s) => ({ ...s, status: "error" }));
      // The browser fires `error` THEN `close`; reconnect lives in onclose.
    };
  }, [scanId, enabled, scheduleFlush]);

  // ── Full-history page-loop ───────────────────────────────────────────
  // Runs on mount and whenever the scan / enabled flag changes. Loads the
  // entire persisted log so the panel's filters see all of it.
  useEffect(() => {
    if (!enabled || !scanId) return;
    const gen = ++historyGenRef.current;
    const token = getToken();
    if (!token) return;

    let cancelled = false;
    setState((s) => ({ ...s, historyLoading: true }));

    (async () => {
      let cursor: { ts: string; id: string } | null = null;
      try {
        for (;;) {
          const page = await fetchLogPage(scanId, token, cursor);
          // Bail if this effect was cleaned up (unmount / scanId flip) or
          // a newer history load superseded it.
          if (cancelled || gen !== historyGenRef.current) return;
          if (page.length > 0) {
            setState((s) => ({ ...s, lines: mergeLines(s.lines, page) }));
            const last = page[page.length - 1];
            cursor = { ts: last.ts, id: last.id };
          }
          if (page.length < HISTORY_PAGE_SIZE) break; // short page → done
        }
      } catch {
        // History load failed — the live WS still fills new lines; leave
        // whatever pages did land in place.
      } finally {
        if (!cancelled && gen === historyGenRef.current) {
          setState((s) => ({ ...s, historyLoading: false }));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [scanId, enabled]);

  // ── WebSocket lifecycle ──────────────────────────────────────────────
  useEffect(() => {
    if (!enabled || !scanId) {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      // Drop the history-loop generation so an in-flight loop bails.
      historyGenRef.current++;
      setState(initialState);
      return;
    }
    mountedRef.current = true;
    connect();

    // Half-open-socket watchdog — force a reconnect if no frame (incl. the
    // server's 30 s ping) has arrived for STALE_MS.
    const watchdog = window.setInterval(() => {
      const ws = wsRef.current;
      if (
        ws &&
        ws.readyState === WebSocket.OPEN &&
        isStreamStale(lastActivityRef.current, Date.now(), STALE_MS)
      ) {
        ws.close();
      }
    }, WATCHDOG_MS);

    return () => {
      mountedRef.current = false;
      window.clearInterval(watchdog);
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [scanId, enabled, connect]);

  return state;
}
