import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Drawer } from "../ui";
import { useScanStream } from "../../hooks/useScanStream";
import { useScanById } from "../../hooks/useScansStream";
import { api } from "../../api/client";
import { useQueryClient } from "@tanstack/react-query";
import type { ScanLogLine } from "../../types";
import {
  LOG_LEVELS,
  DEFAULT_LEVELS,
  filterLogLines,
  scanIsStoppable,
  terminalBadgeVariantFor,
} from "./scanLog";

interface ScanLogPanelProps {
  open: boolean;
  onClose: () => void;
  scanId: string | null;
  sourceName?: string;
}

// Per-line display cap. The scanner's stderr relay batches up to 4 KB
// per chunk; one such chunk rendered with `whitespace-pre-wrap
// break-all` is enough to lock the layout engine when 50 arrive at
// once. The full message stays in memory — only the rendered node is
// bounded.
const DISPLAY_LINE_CAP = 256;

// Maximum rows rendered to the DOM. The buffer in useScanStream holds
// up to 1000; the DOM only ever holds the most recent MAX_VISIBLE_ROWS
// of the *filtered* stream. Capping the rendered count is what keeps
// the layout engine responsive under a heavy log stream.
const MAX_VISIBLE_ROWS = 300;

function truncateForDisplay(s: string): { text: string; truncated: boolean } {
  if (s.length <= DISPLAY_LINE_CAP) return { text: s, truncated: false };
  return { text: s.slice(0, DISPLAY_LINE_CAP), truncated: true };
}

const LEVEL_COLOR: Record<string, string> = {
  info: "text-fg",
  warn: "text-amber-700",
  error: "text-rose-700 dark:text-rose-300",
  stderr: "text-fg-muted",
};

// Dot colour shown on each level filter chip — doubles as a legend.
const LEVEL_DOT: Record<string, string> = {
  info: "bg-sky-500",
  warn: "bg-amber-500",
  error: "bg-rose-500",
  stderr: "bg-gray-400",
};

const LEVEL_LABEL: Record<string, string> = {
  info: "Info",
  warn: "Warn",
  error: "Error",
  stderr: "stderr",
};

const STATUS_LABEL: Record<string, string> = {
  connecting: "Connecting…",
  open: "Live",
  reconnecting: "Reconnecting…",
  closed: "Closed",
  error: "Connection error",
};

const STATUS_COLOR: Record<string, string> = {
  connecting: "bg-amber-500",
  open: "bg-emerald-500",
  reconnecting: "bg-amber-500",
  closed: "bg-gray-400",
  error: "bg-rose-500",
};

// terminalBadge renders the post-completion status hint next to the
// live status pill. Hidden for in-flight scans.
function terminalBadge(scanStatus: string | null | undefined): React.ReactNode {
  const variant = terminalBadgeVariantFor(scanStatus);
  if (variant === null) return null;
  return <Badge variant={variant}>{scanStatus}</Badge>;
}

// Drawer width: "xl" = max-w-4xl. Live log lines are dense and
// path-heavy; the default 672 px jammed long paths against the edge.
const DRAWER_WIDTH = "xl";

export function ScanLogPanel({ open, onClose, scanId, sourceName }: ScanLogPanelProps) {
  const stream = useScanStream(scanId, open);
  const scrollRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const [stopping, setStopping] = useState(false);

  // ── Filters: one unified stream, filtered client-side ────────────
  // Levels: Info/Warn/Error on by default; stderr is raw, noisy
  // passthrough output — opt in via its chip.
  const [levels, setLevels] = useState<Set<string>>(
    () => new Set<string>(DEFAULT_LEVELS),
  );
  const [query, setQuery] = useState("");
  // Scanner filter: empty = every scanner. Only surfaced for
  // multi-scanner scans (more than one scanner has logged).
  const [activeScanners, setActiveScanners] = useState<Set<string>>(() => new Set());
  const [copied, setCopied] = useState(false);

  // ── Follow (tail) state ──────────────────────────────────────────
  const [following, setFollowing] = useState(true);

  // Scan status, preferring the list-level stream (live `scan.state`
  // events incl. the terminal transition) over the per-scan WS
  // snapshot (captured once at connect, never corrected).
  const liveScan = useScanById(scanId);
  const scanStatus = liveScan?.status ?? stream.snapshot?.status ?? null;

  async function handleStop() {
    if (!scanId || stopping || !scanIsStoppable(scanStatus)) return;
    setStopping(true);
    try {
      await api.cancelScan(scanId);
      await queryClient.invalidateQueries({ queryKey: ["sources"] });
      await queryClient.invalidateQueries({ queryKey: ["scans", "active"] });
    } catch {
      // Leave the button enabled for retry.
    } finally {
      setStopping(false);
    }
  }

  // Distinct scanners that have logged on this scan.
  const scannersPresent = useMemo(() => {
    const m = new Map<string, string>();
    for (const l of stream.lines) {
      if (l.scanner_id) {
        m.set(l.scanner_id, l.scanner_name ?? l.scanner_id.slice(0, 8));
      }
    }
    return [...m.entries()].map(([id, name]) => ({ id, name }));
  }, [stream.lines]);

  // The filtered stream + its capped, rendered tail.
  const filtered = useMemo(
    () => filterLogLines(stream.lines, { levels, query, scanners: activeScanners }),
    [stream.lines, levels, query, activeScanners],
  );
  const visibleLines = useMemo(
    () =>
      filtered.length <= MAX_VISIBLE_ROWS
        ? filtered
        : filtered.slice(filtered.length - MAX_VISIBLE_ROWS),
    [filtered],
  );
  const total = stream.lines.length;
  const hiddenOlder = filtered.length - visibleLines.length;
  const filtersNarrow = filtered.length !== total;

  function toggleLevel(level: string) {
    setLevels((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  }

  function toggleScanner(id: string) {
    setActiveScanners((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // ── Auto-follow ──────────────────────────────────────────────────
  // The follow model is intent-based: only a genuine *user* scroll
  // changes it. A programmatic scroll-to-bottom also fires `onScroll`;
  // we stamp the time of our own scroll and ignore the event it
  // produces, so a line appended mid-scroll can't flip follow off by
  // itself (the old auto-scroll bug).
  const followingRef = useRef(following);
  followingRef.current = following;
  const programmaticAtRef = useRef(0);
  const lastScrolledLenRef = useRef(0);
  const scrollTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!following || !scrollRef.current) return;
    if (visibleLines.length === lastScrolledLenRef.current) return;
    lastScrolledLenRef.current = visibleLines.length;
    if (scrollTimerRef.current != null) return;
    // Throttle the scroll-to-bottom to ≤4 Hz — below the perception
    // threshold for "instant", well above the layout-cost danger zone.
    scrollTimerRef.current = window.setTimeout(() => {
      scrollTimerRef.current = null;
      const el = scrollRef.current;
      if (!el || !followingRef.current) return;
      programmaticAtRef.current = Date.now();
      el.scrollTop = el.scrollHeight;
    }, 250);
    return () => {
      if (scrollTimerRef.current != null) {
        window.clearTimeout(scrollTimerRef.current);
        scrollTimerRef.current = null;
      }
    };
  }, [visibleLines, following]);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    // Ignore the scroll event our own programmatic scroll produced.
    if (Date.now() - programmaticAtRef.current < 150) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setFollowing((prev) => (prev === atBottom ? prev : atBottom));
  }

  // Lines that have arrived since follow was paused — drives the
  // floating pill's "N new lines" label.
  const pausedAtRef = useRef(0);
  useEffect(() => {
    if (!following) pausedAtRef.current = stream.lines.length;
  }, [following]);
  const newSincePause = following
    ? 0
    : Math.max(0, stream.lines.length - pausedAtRef.current);

  function jumpToLatest() {
    setFollowing(true);
    const el = scrollRef.current;
    if (el) {
      programmaticAtRef.current = Date.now();
      el.scrollTop = el.scrollHeight;
    }
  }

  function handleCopy() {
    const text = filtered
      .map((l) => {
        const t = new Date(l.ts).toLocaleTimeString();
        const who = l.scanner_name ? ` [${l.scanner_name}]` : "";
        return `${t}  ${l.level.toUpperCase()}${who}  ${l.message}`;
      })
      .join("\n");
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => {
        /* clipboard blocked — no-op */
      },
    );
  }

  const batchSize =
    stream.progress?.current_batch_size ?? stream.snapshot?.current_batch_size;
  const pulsing = stream.status === "connecting" || stream.status === "reconnecting";

  const emptyMessage =
    total === 0
      ? stream.status === "open"
        ? "Waiting for scanner output…"
        : stream.status === "reconnecting"
          ? "Reconnecting to the live stream…"
          : "No log lines yet."
      : "No lines match the current filters.";

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={
        <div className="flex items-center gap-2">
          <span>Live scan log</span>
          {sourceName && (
            <span className="text-sm font-normal text-fg-muted">· {sourceName}</span>
          )}
        </div>
      }
      width={DRAWER_WIDTH}
    >
      <div className="flex flex-col h-full px-5 py-4">
        {/* Connection status + Stop */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="relative inline-flex h-2 w-2">
              {pulsing && (
                <span
                  className={`absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping ${STATUS_COLOR[stream.status]}`}
                />
              )}
              <span
                className={`relative inline-flex h-2 w-2 rounded-full ${STATUS_COLOR[stream.status]}`}
              />
            </span>
            <span className="text-xs text-fg-muted">{STATUS_LABEL[stream.status]}</span>
            {terminalBadge(scanStatus)}
            {batchSize != null && (
              <span
                className="text-[10px] font-mono uppercase tracking-wide text-fg-muted bg-surface-muted rounded px-1.5 py-px"
                title="Adaptive ingest batch size — converges toward what the source + API can sustain."
              >
                batch {batchSize}
              </span>
            )}
          </div>
          {scanIsStoppable(scanStatus) && (
            <Button size="sm" variant="danger" onClick={handleStop} loading={stopping}>
              Stop scan
            </Button>
          )}
        </div>

        {/* Toolbar: search · level filters · copy */}
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the log…"
            aria-label="Search the log"
            className="h-7 min-w-[9rem] flex-1 rounded-md border border-line bg-app px-2.5 text-xs text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent-500 focus:border-accent-400"
          />
          <div className="flex items-center gap-1.5">
            {LOG_LEVELS.map((level) => (
              <FilterChip
                key={level}
                active={levels.has(level)}
                onClick={() => toggleLevel(level)}
                dotClass={LEVEL_DOT[level]}
                label={LEVEL_LABEL[level]}
                title={
                  levels.has(level)
                    ? `Hide ${LEVEL_LABEL[level]} lines`
                    : `Show ${LEVEL_LABEL[level]} lines`
                }
              />
            ))}
          </div>
          <Button
            size="sm"
            variant="secondary"
            onClick={handleCopy}
            disabled={filtered.length === 0}
            reserveLabel="Copied"
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>

        {/* Scanner filter — multi-scanner scans only */}
        {scannersPresent.length > 1 && (
          <div className="flex flex-wrap items-center gap-1.5 mb-2">
            <span className="text-[11px] uppercase tracking-wide text-fg-subtle mr-0.5">
              Scanners
            </span>
            {scannersPresent.map((s) => (
              <FilterChip
                key={s.id}
                active={activeScanners.size === 0 || activeScanners.has(s.id)}
                onClick={() => toggleScanner(s.id)}
                dotClass={scannerDotColor(s.id)}
                label={s.name}
                title={`Show only ${s.name}`}
              />
            ))}
          </div>
        )}

        {/* Count line */}
        <div className="flex items-center justify-between text-[11px] text-fg-subtle mb-1">
          <span>
            {filtersNarrow
              ? `${filtered.length.toLocaleString()} of ${total.toLocaleString()} lines`
              : `${total.toLocaleString()} line${total === 1 ? "" : "s"}`}
          </span>
          {hiddenOlder > 0 && (
            <span>showing the most recent {visibleLines.length.toLocaleString()}</span>
          )}
        </div>

        {/* Log tail + floating "jump to latest" pill */}
        <div className="relative flex-1 min-h-[400px] max-h-[70vh]">
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="h-full overflow-y-auto bg-app rounded-md font-mono text-xs leading-snug px-4 py-3 border border-line"
          >
            {visibleLines.length === 0 ? (
              <p className="text-fg-subtle italic">{emptyMessage}</p>
            ) : (
              visibleLines.map((line) => <LogRow key={line.id} line={line} />)
            )}
          </div>
          {!following && (
            <button
              type="button"
              onClick={jumpToLatest}
              className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 inline-flex items-center gap-1.5 rounded-full bg-accent-600 px-3.5 py-1.5 text-xs font-medium text-white shadow-lg shadow-black/20 hover:bg-accent-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-app"
            >
              <span aria-hidden>↓</span>
              {newSincePause > 0
                ? `${newSincePause.toLocaleString()} new line${newSincePause === 1 ? "" : "s"}`
                : "Jump to latest"}
            </button>
          )}
        </div>
      </div>
    </Drawer>
  );
}

// FilterChip — a pill toggle for the level / scanner filters. Local to
// this panel (not a shared `ui` primitive). Active = accent-tinted;
// the colour dot doubles as a legend.
function FilterChip({
  active,
  onClick,
  dotClass,
  label,
  title,
}: {
  active: boolean;
  onClick: () => void;
  dotClass?: string;
  label: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 ${
        active
          ? "border-accent-300 bg-accent-50 text-fg dark:border-accent-500/40 dark:bg-accent-500/15"
          : "border-line text-fg-muted hover:bg-surface-muted hover:text-fg"
      }`}
    >
      {dotClass && (
        <span
          className={`h-2 w-2 rounded-full ${dotClass} ${active ? "" : "opacity-40"}`}
        />
      )}
      {label}
    </button>
  );
}

// v0.28.2 — palette derived from a hash of scanner_id so the same
// scanner gets the same colour across reloads / tabs, without
// persisting any preference.
const SCANNER_PILL_COLORS = [
  "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300",
  "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
  "bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300",
  "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300",
  "bg-cyan-100 text-cyan-800 dark:bg-cyan-500/15 dark:text-cyan-300",
  "bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-500/15 dark:text-fuchsia-300",
  "bg-lime-100 text-lime-800 dark:bg-lime-500/15 dark:text-lime-300",
];

// Solid dot colours, parallel to SCANNER_PILL_COLORS, for the scanner
// filter chips.
const SCANNER_DOT_COLORS = [
  "bg-sky-500",
  "bg-emerald-500",
  "bg-violet-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-cyan-500",
  "bg-fuchsia-500",
  "bg-lime-500",
];

function scannerHash(scannerId: string): number {
  // Simple djb2-ish hash.
  let h = 5381;
  for (let i = 0; i < scannerId.length; i++) {
    h = ((h << 5) + h + scannerId.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

function scannerPillColor(scannerId: string): string {
  return SCANNER_PILL_COLORS[scannerHash(scannerId) % SCANNER_PILL_COLORS.length];
}

function scannerDotColor(scannerId: string): string {
  return SCANNER_DOT_COLORS[scannerHash(scannerId) % SCANNER_DOT_COLORS.length];
}

/**
 * Per-row React.memo wrapper. Lines are append-only and immutable once
 * buffered, so the memo (shallow equality on `line`) short-circuits
 * every existing row — only newly-appended rows mount on a WS frame.
 */
const LogRow = memo(function LogRow({ line }: { line: ScanLogLine }) {
  const ts = useMemo(
    () =>
      new Date(line.ts).toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
    [line.ts],
  );
  const display = useMemo(() => truncateForDisplay(line.message), [line.message]);
  const colorClass = LEVEL_COLOR[line.level] ?? "text-fg";
  const scannerLabel =
    line.scanner_name ?? (line.scanner_id ? line.scanner_id.slice(0, 8) : null);

  return (
    <div className="flex gap-2">
      <span className="text-fg-subtle shrink-0 w-20">{ts}</span>
      <span className={`shrink-0 w-12 uppercase font-semibold ${colorClass}`}>
        {line.level}
      </span>
      {scannerLabel && line.scanner_id && (
        <span
          className={`shrink-0 px-1.5 py-px rounded text-[10px] font-mono uppercase tracking-wide ${scannerPillColor(line.scanner_id)}`}
          title={`scanner: ${scannerLabel}`}
        >
          {scannerLabel}
        </span>
      )}
      <span
        // min-w-0 lets the flex item shrink below its intrinsic content
        // width so break-all wraps; pr-2 reserves a right gutter.
        className={`min-w-0 flex-1 pr-2 whitespace-pre-wrap break-all ${colorClass}`}
      >
        {display.text}
        {display.truncated && (
          <span className="text-fg-subtle italic ml-1">
            … (+{(line.message.length - DISPLAY_LINE_CAP).toLocaleString()} chars)
          </span>
        )}
      </span>
    </div>
  );
});
