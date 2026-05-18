/**
 * Pure, render-free helpers for the scan Live Log panel — extracted so
 * the node-env vitest suite can cover them without a DOM. v0.32.0.
 */
import type { ScanLogLine } from "../../types";
import type { BadgeVariant } from "../ui";

export type LogLevel = ScanLogLine["level"];

/** Every level the unified stream can carry, in display order. */
export const LOG_LEVELS: readonly LogLevel[] = ["info", "warn", "error", "stderr"];

/**
 * Levels shown by default. `stderr` is raw, often-noisy passthrough
 * output — opt in via its filter chip rather than defaulting it on.
 */
export const DEFAULT_LEVELS: readonly LogLevel[] = ["info", "warn", "error"];

export interface LogFilter {
  /** Levels to include. */
  levels: ReadonlySet<string>;
  /** Case-insensitive substring match on the message; "" = no search. */
  query: string;
  /** Scanner ids to include; empty = every scanner. */
  scanners: ReadonlySet<string>;
}

/**
 * Filter the log buffer for the unified stream view. Pure — callers
 * memoise on (lines, filter). A line is kept when its level is enabled,
 * it matches the search query, and — when a scanner filter is active —
 * it was produced by one of the selected scanners.
 */
export function filterLogLines(
  lines: readonly ScanLogLine[],
  filter: LogFilter,
): ScanLogLine[] {
  const q = filter.query.trim().toLowerCase();
  const out: ScanLogLine[] = [];
  for (const line of lines) {
    if (!filter.levels.has(line.level)) continue;
    if (filter.scanners.size > 0) {
      if (!line.scanner_id || !filter.scanners.has(line.scanner_id)) continue;
    }
    if (q && !line.message.toLowerCase().includes(q)) continue;
    out.push(line);
  }
  return out;
}

/**
 * True when a streaming WebSocket has gone silent past `staleMs` — the
 * client's half-open-socket signal. `lastActivityMs` is the epoch-ms of
 * the last frame received; a server `ping` counts (it exists precisely
 * so the client can tell a quiet socket is still alive). Null before
 * the first frame → not stale.
 */
export function isStreamStale(
  lastActivityMs: number | null,
  now: number,
  staleMs: number,
): boolean {
  return lastActivityMs != null && now - lastActivityMs > staleMs;
}

/**
 * scanIsStoppable — true only while a scan can still be cancelled
 * (pending or running). The Stop button gates on this, NOT on the
 * WebSocket connection state: the WS stays open after a scan ends,
 * which would otherwise leave the button live for a finished scan.
 */
export function scanIsStoppable(scanStatus: string | null | undefined): boolean {
  return scanStatus === "running" || scanStatus === "pending";
}

/**
 * Badge variant for the post-completion terminal-status hint shown
 * next to the live status pill. Returns null for in-flight / unknown
 * states — the live pill already describes those.
 */
export function terminalBadgeVariantFor(
  scanStatus: string | null | undefined,
): BadgeVariant | null {
  if (scanStatus === "completed") return "online";
  if (scanStatus === "failed") return "failed";
  if (scanStatus === "cancelled") return "neutral";
  return null;
}
