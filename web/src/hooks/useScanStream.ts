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
              setState((s) => ({
                ...s,
                lines: capLines([...s.lines, ...lines]),
                lastEventTs: lines[lines.length - 1]?.ts ?? s.lastEventTs,
              }));
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

      setState((s) => {
        if (event.kind === "snapshot") {
          return {
            ...s,
            snapshot: event,
            lines: capLines(event.recent_lines ?? []),
            lastEventTs:
              event.recent_lines && event.recent_lines.length > 0
                ? event.recent_lines[event.recent_lines.length - 1].ts
                : s.lastEventTs,
          };
        }
        if (event.kind === "progress") {
          return {
            ...s,
            progress: event,
            lastEventTs: event.ts ?? s.lastEventTs,
          };
        }
        if (event.kind === "log" || event.kind === "stderr") {
          const next = capLines([...s.lines, ...(event.lines ?? [])]);
          const lastTs = event.lines?.[event.lines.length - 1]?.ts ?? s.lastEventTs;
          return { ...s, lines: next, lastEventTs: lastTs };
        }
        return s;
      });
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
