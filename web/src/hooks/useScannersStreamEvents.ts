/**
 * Reference-counted WebSocket subscription to /ws/scanners.
 *
 * Mirrors the structure of useScansStreamEvents but talks to the
 * admin-only scanner-lifecycle stream — different auth surface, so
 * the two hooks deliberately don't share a socket. Used by the
 * SettingsScanners page (pending-claims pane + join-token wizard's
 * "waiting for scanner" step).
 */
import { useEffect, useRef } from "react";

import { getToken } from "../api/client";

export interface PendingDiscoverySnapshot {
  id: string;
  pairing_code: string;
  hostname: string | null;
  agent_version: string | null;
  requested_pool: string | null;
  requested_at: string;
  expires_at: string;
  key_fingerprint: string;
}

export type ScannersStreamEvent =
  | { kind: "snapshot"; pending_discoveries: PendingDiscoverySnapshot[] }
  | { kind: "scanner.claim_redeemed"; scanner_id: string; scanner_name: string;
      pool: string; token_id: string }
  | { kind: "scanner.discovery_requested"; discovery_id: string;
      pairing_code: string; hostname: string | null;
      agent_version: string | null; requested_pool: string | null;
      expires_at: string; key_fingerprint: string }
  | { kind: "scanner.discovery_approved"; discovery_id: string;
      scanner_id: string; scanner_name: string; pool: string }
  | { kind: "scanner.discovery_denied"; discovery_id: string;
      deny_reason: string | null }
  | { kind: "scanner.discovery_expired"; discovery_id: string }
  | { kind: "scanner.registered"; scanner_id: string }
  | { kind: "scanner.deleted"; scanner_id: string }
  | { kind: "ping" }
  | { kind: "error"; message: string };

type Listener = (event: ScannersStreamEvent) => void;

const listeners = new Set<Listener>();
let ws: WebSocket | null = null;
let reconnectTimer: number | null = null;
let visibilityBound = false;

function buildUrl(): string | null {
  const token = getToken();
  if (!token) return null;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/scanners?token=${encodeURIComponent(token)}`;
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
    try {
      const event = JSON.parse(msg.data) as ScannersStreamEvent;
      dispatch(event);
    } catch {
      // Malformed frame; ignore — live stream is best-effort.
    }
  };
  sock.onclose = () => {
    if (ws === sock) ws = null;
    if (listeners.size === 0) return;
    scheduleReconnect();
  };
  sock.onerror = () => {
    try { sock.close(); } catch { /* noop */ }
  };
}

function scheduleReconnect() {
  if (reconnectTimer != null) return;
  const delay = 1000 + Math.random() * 4000;
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
