/**
 * Reference-counted WebSocket subscription to /ws/scans.
 *
 * Multiple consumers (Sources page + Dashboard) share one socket per
 * tab. The first useScansStreamEvents() opens the WS; the last to
 * unmount closes it. Each consumer registers an event handler and
 * receives every server-pushed event verbatim — they're free to
 * reduce / filter as they please.
 *
 * Reconnect: on non-graceful close, schedule a reopen with 1-5s
 * backoff. On visibility change to hidden, close (browser already
 * suspends timers; we save the token-bound socket for re-auth too).
 * On reopen, the server sends a fresh `snapshot` frame and consumers
 * dispatch on that to replace stale state.
 */
import { useEffect, useRef } from "react";

import { getToken } from "../api/client";

export type ScansStreamEvent =
  | { kind: "snapshot"; scans: SnapshotScan[] }
  | { kind: "scan.state"; source_id: string; scan_id: string;
      scan_status: "pending" | "running" | "completed" | "failed" | "cancelled";
      source_status: string;
      scanner_id: string | null; scanner_name: string | null;
      scan_type: string; files_found: number; current_path: string | null }
  | { kind: "source.created"; source_id: string; source_status: string;
      name: string; type: string }
  | { kind: "source.deleted"; source_id: string }
  | { kind: "ping" }
  | { kind: "error"; message: string };

export interface SnapshotScan {
  scan_id: string;
  source_id: string;
  scan_status: string;
  source_status: string;
  scanner_id: string | null;
  scanner_name: string | null;
  scan_type: string;
  files_found: number;
  current_path: string | null;
  started_at: string | null;
}

type Listener = (event: ScansStreamEvent) => void;

// Module-singleton state. The hook just registers / unregisters
// listeners; the socket lifecycle is shared.
const listeners = new Set<Listener>();
let ws: WebSocket | null = null;
let reconnectTimer: number | null = null;
let visibilityBound = false;
// Capped exponential backoff (v0.4.3). Resets to 0 on any
// successful frame from the server, so a transient blip doesn't
// poison subsequent reconnects for the rest of the session.
let retryCount = 0;
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 30_000;

function buildUrl(): string | null {
  const token = getToken();
  if (!token) return null;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  // The api is reverse-proxied at /api in dev; the WS lives at /ws.
  return `${proto}//${window.location.host}/ws/scans?token=${encodeURIComponent(token)}`;
}

function dispatch(event: ScansStreamEvent) {
  for (const fn of listeners) fn(event);
}

function open() {
  if (ws || reconnectTimer != null) return;
  const url = buildUrl();
  if (!url) return; // no token; consumers will see no events until login
  if (typeof document !== "undefined" && document.hidden) return;
  const sock = new WebSocket(url);
  ws = sock;
  sock.onmessage = (msg) => {
    // Any successful frame from the server means the connection is
    // healthy — reset backoff so a future transient blip starts
    // from 1s again, not from wherever we'd escalated to.
    retryCount = 0;
    try {
      const event = JSON.parse(msg.data) as ScansStreamEvent;
      dispatch(event);
    } catch {
      // Malformed frame; ignore (live stream is best-effort).
    }
  };
  sock.onclose = () => {
    if (ws === sock) ws = null;
    if (listeners.size === 0) return; // nothing to reconnect for
    scheduleReconnect();
  };
  sock.onerror = () => {
    // Let onclose handle the reconnect; just close cleanly.
    try { sock.close(); } catch { /* noop */ }
  };
}

function scheduleReconnect() {
  if (reconnectTimer != null) return;
  // Capped exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s, 30s, …
  // with ±20% jitter so a fleet of browsers reconnecting after an
  // api outage doesn't synchronise into a thundering herd. Reset
  // happens in onmessage (any frame counts as "we're healthy").
  const base = Math.min(RETRY_BASE_MS * 2 ** retryCount, RETRY_MAX_MS);
  const jitter = base * 0.2 * (2 * Math.random() - 1);
  const delay = base + jitter;
  retryCount++;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    open();
  }, delay);
}

function close() {
  if (reconnectTimer != null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    try { ws.close(); } catch { /* noop */ }
    ws = null;
  }
}

function bindVisibility() {
  if (visibilityBound || typeof document === "undefined") return;
  visibilityBound = true;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) close();
    else if (listeners.size > 0) open();
  });
}

export function useScansStreamEvents(onEvent: Listener) {
  // Stable ref so identity changes between renders don't churn the
  // listener set.
  const ref = useRef(onEvent);
  ref.current = onEvent;

  useEffect(() => {
    const fn: Listener = (e) => ref.current(e);
    listeners.add(fn);
    bindVisibility();
    open(); // refcount-via-set; no-op when already open
    return () => {
      listeners.delete(fn);
      if (listeners.size === 0) close();
    };
  }, []);
}
