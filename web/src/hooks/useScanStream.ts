import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "../api/client";
import type {
  ScanLogLine,
  ScanProgressEvent,
  ScanSnapshot,
  ScanWsEvent,
} from "../types";

const MAX_BUFFERED_LINES = 1000;

export interface ScanStreamState {
  snapshot: ScanSnapshot | null;
  progress: ScanProgressEvent | null;
  // Combined log+stderr buffer — components filter by `level !== "stderr"`
  // for the Activity tab and `level === "stderr"` for the Raw stderr tab.
  lines: ScanLogLine[];
  status: "connecting" | "open" | "closed" | "error";
  // Helps debugging: the last ts we successfully received, useful as
  // the `since` cursor on reconnect backfill (HTTP path).
  lastEventTs: string | null;
}

const initialState: ScanStreamState = {
  snapshot: null,
  progress: null,
  lines: [],
  status: "connecting",
  lastEventTs: null,
};

/**
 * useScanStream — opens a WebSocket to /ws/scans/{id}, parses the snapshot
 * and live events, and exposes a single state object the consumer can
 * render from. Reconnects with exponential backoff on close. Backfills
 * any missed log lines via GET /api/scans/{id}/log on reconnect.
 *
 * Set `enabled=false` to suspend the connection (e.g., when the panel is
 * unmounted or the scan has finished).
 */
export function useScanStream(scanId: string | null, enabled: boolean = true) {
  const [state, setState] = useState<ScanStreamState>(initialState);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const attemptRef = useRef(0);

  // Inbound-event coalescing buffer. A burst of WS messages (common with
  // a chatty stderr relay or a fast scan) used to trigger one setState
  // per message — each rebuilding a 1000-line array and forcing a full
  // re-render of the log panel. With per-message setState that's a
  // layout-thrash death spiral. We instead batch: append events to a
  // pending ref and flush once per animation frame (≤16 ms cadence).
  // The DOM gets updated at most once per paint regardless of how many
  // messages arrived in between.
  const pendingRef = useRef<{
    progress: ScanProgressEvent | null;
    snapshot: ScanSnapshot | null;
    appendLines: ScanLogLine[];
    replaceLines: ScanLogLine[] | null; // set by snapshot — replaces buffer
  }>({ progress: null, snapshot: null, appendLines: [], replaceLines: null });
  const flushScheduledRef = useRef(false);

  const scheduleFlush = useCallback(() => {
    if (flushScheduledRef.current) return;
    flushScheduledRef.current = true;
    requestAnimationFrame(() => {
      flushScheduledRef.current = false;
      const pending = pendingRef.current;
      pendingRef.current = {
        progress: null,
        snapshot: null,
        appendLines: [],
        replaceLines: null,
      };
      if (
        !pending.progress &&
        !pending.snapshot &&
        pending.appendLines.length === 0 &&
        pending.replaceLines === null
      ) {
        return;
      }
      setState((s) => {
        let lines = s.lines;
        let lastEventTs = s.lastEventTs;
        if (pending.replaceLines !== null) {
          lines = capLines(pending.replaceLines);
          if (lines.length > 0) lastEventTs = lines[lines.length - 1].ts;
        }
        if (pending.appendLines.length > 0) {
          lines = capLines([...lines, ...pending.appendLines]);
          lastEventTs =
            pending.appendLines[pending.appendLines.length - 1].ts ?? lastEventTs;
        }
        if (pending.progress) {
          lastEventTs = pending.progress.ts ?? lastEventTs;
        }
        return {
          ...s,
          snapshot: pending.snapshot ?? s.snapshot,
          progress: pending.progress ?? s.progress,
          lines,
          lastEventTs,
        };
      });
    });
  }, []);

  // Latest line ts for reconnect backfill. Stored in a ref so `connect`
  // can read it without re-creating itself on every state update.
  const sinceRef = useRef<string | null>(null);
  useEffect(() => {
    sinceRef.current = state.lastEventTs;
  }, [state.lastEventTs]);

  // `enabled` mirrored into a ref so the WS `onclose` handler reads the
  // current value rather than the value captured when the closure was
  // created. Without this, a hook whose `enabled` flips false right
  // before the socket closes would schedule a reconnect that fires
  // post-unmount, leaking a connection.
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

    // Build ws:// or wss:// from the page origin so we work in dev (Vite
    // proxy) and prod (same-origin behind nginx).
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/scans/${scanId}?token=${encodeURIComponent(token)}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    setState((s) => ({ ...s, status: "connecting" }));

    ws.onopen = async () => {
      attemptRef.current = 0;
      setState((s) => ({ ...s, status: "open" }));
      // Backfill anything that landed between drop and reconnect.
      const since = sinceRef.current;
      if (since) {
        try {
          const resp = await fetch(
            `/api/scans/${scanId}/log?since=${encodeURIComponent(since)}&kind=all`,
            { headers: { Authorization: `Bearer ${token}` } }
          );
          if (resp.ok) {
            const lines: ScanLogLine[] = await resp.json();
            if (lines.length) {
              pendingRef.current.appendLines.push(...lines);
              scheduleFlush();
            }
          }
        } catch {
          // Backfill failure is non-fatal — live stream takes over.
        }
      }
    };

    ws.onmessage = (ev) => {
      let event: ScanWsEvent;
      try {
        event = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (event.kind === "ping") return;

      // Accumulate into the per-frame buffer rather than calling
      // setState directly. A burst of 50 stderr messages becomes ONE
      // re-render at the next animation frame, not 50.
      if (event.kind === "snapshot") {
        pendingRef.current.snapshot = event;
        pendingRef.current.replaceLines = event.recent_lines ?? [];
        // Snapshot replaces the buffer, so any pre-snapshot appends
        // queued in the same frame would just get overwritten. Drop them
        // to make that explicit.
        pendingRef.current.appendLines = [];
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
      // Read enabled via ref — closure-captured value would be stale if
      // the consumer toggled enabled between connect() and onclose.
      if (!enabledRef.current) {
        setState((s) => ({ ...s, status: "closed" }));
        return;
      }
      // Exponential backoff capped at 10 s.
      attemptRef.current += 1;
      const delay = Math.min(1000 * 2 ** (attemptRef.current - 1), 10_000);
      setState((s) => ({ ...s, status: "connecting" }));
      reconnectTimer.current = window.setTimeout(connect, delay);
    };

    ws.onerror = () => {
      setState((s) => ({ ...s, status: "error" }));
      // The browser fires `error` THEN `close` — reconnect logic lives
      // in onclose to avoid double-scheduling.
    };
  }, [scanId, enabled]);

  useEffect(() => {
    if (!enabled || !scanId) {
      // Tear down any existing socket when disabled.
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      setState(initialState);
      return;
    }
    connect();
    return () => {
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

function capLines(lines: ScanLogLine[]): ScanLogLine[] {
  if (lines.length <= MAX_BUFFERED_LINES) return lines;
  return lines.slice(lines.length - MAX_BUFFERED_LINES);
}
