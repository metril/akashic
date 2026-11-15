/**
 * Reference-counted WebSocket subscription to /ws/v1/scanners.
 *
 * Mirrors the structure of useScansStreamEvents but talks to the
 * admin-only scanner-lifecycle stream — different auth surface, so
 * the two hooks deliberately don't share a socket. Used by the
 * SettingsScanners page (pending-claims pane + join-token wizard's
 * "waiting for scanner" step).
 */
import { useEffect, useRef } from "react";

import { WS_BASE, completeSsoLogin, getToken } from "../api/client";
import type { PendingDiscoverySnapshot, ScannersStreamEvent } from "../api/types";

export type { PendingDiscoverySnapshot, ScannersStreamEvent };

type Listener = (event: ScannersStreamEvent) => void;

const listeners = new Set<Listener>();
let ws: WebSocket | null = null;
let reconnectTimer: number | null = null;
let visibilityBound = false;
// Capped exponential backoff (v0.4.3) — same rationale as
// useScansStreamEvents. Resets on any successful frame.
let retryCount = 0;
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 30_000;

function buildUrl(): string | null {
  const token = getToken();
  if (!token) return null;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${WS_BASE}/scanners?token=${encodeURIComponent(token)}`;
}

function dispatch(event: ScannersStreamEvent) {
  for (const fn of listeners) fn(event);
}

function open() {
  if (ws || reconnectTimer != null) return;
  const url = buildUrl();
  if (!url) return;
  if (typeof document !== "undefined" && document.hidden) return;
  const sock = new WebSocket(url);
  ws = sock;
  sock.onmessage = (msg) => {
    retryCount = 0; // healthy connection resets backoff
    try {
      const event = JSON.parse(msg.data) as ScannersStreamEvent;
      dispatch(event);
    } catch {
      // Malformed frame; ignore — live stream is best-effort.
    }
  };
  sock.onclose = (ev) => {
    if (ws === sock) ws = null;
    if (listeners.size === 0) return;
    if (ev.code === 1008) {
      // Expired access token: refresh before reconnecting, else the
      // server rejects every retry the same way.
      void completeSsoLogin().then(() => {
        if (listeners.size > 0) scheduleReconnect();
      });
      return;
    }
    scheduleReconnect();
  };
  sock.onerror = () => {
    try { sock.close(); } catch { /* noop */ }
  };
}

function scheduleReconnect() {
  if (reconnectTimer != null) return;
  // Capped exponential 1s → 30s with ±20% jitter. See
  // useScansStreamEvents for the full rationale.
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

export function useScannersStreamEvents(onEvent: Listener) {
  const ref = useRef(onEvent);
  ref.current = onEvent;

  useEffect(() => {
    const fn: Listener = (e) => ref.current(e);
    listeners.add(fn);
    bindVisibility();
    open();
    return () => {
      listeners.delete(fn);
      if (listeners.size === 0) close();
    };
  }, []);
}
