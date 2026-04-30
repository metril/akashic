import { useEffect, useMemo, useRef, useState } from "react";
import { Drawer } from "../ui";
import { useScanStream } from "../../hooks/useScanStream";

interface ScanLogPanelProps {
  open: boolean;
  onClose: () => void;
  scanId: string | null;
  sourceName?: string;
}

type Tab = "activity" | "stderr";

// Per-line display cap. The scanner's stderr relay batches up to 4 KB per
// chunk, and a single such chunk rendered with `whitespace-pre-wrap
// break-all` is enough to lock up the layout engine when 50 of them
// arrive at once. The full message stays in memory; only the rendered
// node is bounded. Users who need the full text can copy/expand later.
const DISPLAY_LINE_CAP = 1024;
function truncateForDisplay(s: string): { text: string; truncated: boolean } {
  if (s.length <= DISPLAY_LINE_CAP) return { text: s, truncated: false };
  return { text: s.slice(0, DISPLAY_LINE_CAP), truncated: true };
}

const LEVEL_COLOR: Record<string, string> = {
  info: "text-gray-700",
  warn: "text-amber-700",
  error: "text-rose-700",
  stderr: "text-gray-600",
};

const STATUS_LABEL: Record<string, string> = {
  connecting: "Connecting…",
  open: "Live",
  closed: "Closed",
  error: "Connection error",
};

const STATUS_COLOR: Record<string, string> = {
  connecting: "bg-amber-500",
  open: "bg-emerald-500",
  closed: "bg-gray-400",
  error: "bg-rose-500",
};

export function ScanLogPanel({ open, onClose, scanId, sourceName }: ScanLogPanelProps) {
  const stream = useScanStream(scanId, open);
  const [tab, setTab] = useState<Tab>("activity");
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Single pass over the buffer to compute both filtered subsets AND
  // their counts. The previous impl called .filter() three times per
  // render (twice for the count badges, once for the visible list);
  // with a chatty stream that adds up.
  const { activityLines, stderrLines } = useMemo(() => {
    const activity: typeof stream.lines = [];
    const stderr: typeof stream.lines = [];
    for (const line of stream.lines) {
      if (line.level === "stderr") stderr.push(line);
      else activity.push(line);
    }
    return { activityLines: activity, stderrLines: stderr };
  }, [stream.lines]);

  const visibleLines = tab === "activity" ? activityLines : stderrLines;

  // Auto-scroll without forcing a layout pass on every render. We only
  // touch scrollTop if visibleLines actually changed, and we read
  // scrollHeight inside a rAF so it batches with the paint that just
  // rendered the new rows. The previous effect ran on a length change,
  // which triggered scrollHeight reads inside React's commit phase —
  // exactly when layout is most expensive.
  useEffect(() => {
    if (!autoScroll || !scrollRef.current) return;
    const el = scrollRef.current;
    const id = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [visibleLines, autoScroll]);

  function onScroll() {
    if (!scrollRef.current) return;
    const el = scrollRef.current;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    // Re-engage auto-scroll once the user manually scrolls back to
    // the bottom; pause it as soon as they scroll up.
    setAutoScroll(atBottom);
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={
        <div className="flex items-center gap-2">
          <span>Live scan log</span>
          {sourceName && (
            <span className="text-sm font-normal text-gray-500">· {sourceName}</span>
          )}
        </div>
      }
      width="lg"
    >
      <div className="flex flex-col h-full">
        {/* Status pill */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${STATUS_COLOR[stream.status]}`}
            />
            <span className="text-xs text-gray-600">{STATUS_LABEL[stream.status]}</span>
          </div>
          <button
            type="button"
            onClick={() => {
              setAutoScroll(true);
              if (scrollRef.current) {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
              }
            }}
            className="text-xs text-gray-500 hover:text-gray-700 underline disabled:opacity-50"
            disabled={autoScroll}
          >
            {autoScroll ? "Auto-scrolling" : "Resume tail"}
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-200 mb-2 text-sm">
          <button
            type="button"
            onClick={() => setTab("activity")}
            className={`px-3 py-1.5 -mb-px border-b-2 ${
              tab === "activity"
                ? "border-gray-900 text-gray-900 font-medium"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            Activity
            <span className="ml-1.5 text-xs text-gray-400">
              ({activityLines.length})
            </span>
          </button>
          <button
            type="button"
            onClick={() => setTab("stderr")}
            className={`px-3 py-1.5 -mb-px border-b-2 ${
              tab === "stderr"
                ? "border-gray-900 text-gray-900 font-medium"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            Raw stderr
            <span className="ml-1.5 text-xs text-gray-400">
              ({stderrLines.length})
            </span>
          </button>
        </div>

        {/* Tail */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex-1 min-h-[400px] max-h-[70vh] overflow-y-auto bg-gray-50 rounded-md font-mono text-xs leading-snug p-3 border border-gray-200"
        >
          {visibleLines.length === 0 ? (
            <p className="text-gray-400 italic">
              {stream.status === "open" ? "Waiting for output…" : "No log lines yet."}
            </p>
          ) : (
            visibleLines.map((line) => {
              const display = truncateForDisplay(line.message);
              return (
                <div key={line.id} className="flex gap-2">
                  <span className="text-gray-400 shrink-0 w-20">
                    {new Date(line.ts).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })}
                  </span>
                  {tab === "activity" && (
                    <span
                      className={`shrink-0 w-12 uppercase font-semibold ${LEVEL_COLOR[line.level] ?? "text-gray-700"}`}
                    >
                      {line.level}
                    </span>
                  )}
                  <span
                    className={`whitespace-pre-wrap break-all ${LEVEL_COLOR[line.level] ?? "text-gray-800"}`}
                  >
                    {display.text}
                    {display.truncated && (
                      <span className="text-gray-400 italic ml-1">
                        … (+{(line.message.length - DISPLAY_LINE_CAP).toLocaleString()} chars)
                      </span>
                    )}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </Drawer>
  );
}
