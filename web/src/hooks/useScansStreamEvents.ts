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
      scan_type: string; files_found: number; current_path: string | null;
      // v0.4.10 — present on lease + heartbeat + complete (i.e.
      // every event after the scanner has actually started). Trigger
      // events still send null since started_at isn't set yet at
      // pending. Surfaces in bySource[id].started_at so ETA + the
      // recomputeBySource ordering have the real timestamp.
      started_at?: string | null }
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

// Half-open-socket watchdog. A WebSocket can stop delivering frames
// without ever firing `close`/`error` — it just sits in readyState
// OPEN. The server pings every 30 s, so if no frame (incl. ping) has
// arrived for STALE_MS the socket is dead: force-close it and let
// onclose run the normal backoff reconnect. v0.32.0.
let watchdogTimer: number | null = null;
let lastFrameAt = 0;
const STALE_MS = 45_000;
const WATCHDOG_MS = 15_000;
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
  lastFrameAt = Date.now();
  startWatchdog();
  sock.onopen = () => {
    lastFrameAt = Date.now();
  };
  sock.onmessage = (msg) => {
    // Any successful frame from the server means the connection is
    // healthy — reset backoff so a future transient blip starts
    // from 1s again, not from wherever we'd escalated to. The
    // timestamp also feeds the half-open watchdog (a `ping` counts).
    retryCount = 0;
    lastFrameAt = Date.now();
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

function startWatchdog() {
  if (watchdogTimer != null) return;
  watchdogTimer = window.setInterval(() => {
    if (
      ws &&
      ws.readyState === WebSocket.OPEN &&
      Date.now() - lastFrameAt > STALE_MS
    ) {
      // Dead-but-OPEN socket — force the close so onclose reconnects.
      try { ws.close(); } catch { /* noop */ }
    }
  }, WATCHDOG_MS);
}

function stopWatchdog() {
  if (watchdogTimer != null) {
    window.clearInterval(watchdogTimer);
    watchdogTimer = null;
  }
}

function close() {
  stopWatchdog();
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

/**
 * Non-React entry point used by the module-singleton store in
 * useScansStream.ts (v0.4.5). Lets the store wire its single
 * dispatch callback without going through React lifecycle, so N
 * mounted components share ONE reducer pass per WS frame instead
 * of running the reducer N times.
 *
 * Behaviour mirrors useScansStreamEvents: ref-counted into the same
 * listeners set, opens the WS on first add, closes on last remove.
 */
export function subscribeRawScansEvents(cb: Listener): () => void {
  listeners.add(cb);
  bindVisibility();
  open();
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0) close();
  };
}
