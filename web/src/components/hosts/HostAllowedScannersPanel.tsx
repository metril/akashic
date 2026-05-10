import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useHostScannerSummary,
  useTestHostShares,
  useUpdateHostAllowedScanners,
  type HostScannerSummaryRow,
} from "../../hooks/useHosts";
import {
  Button,
  ReachabilityDot,
  Spinner,
  type ReachabilityState,
} from "../ui";

interface Props {
  hostId: string;
  attachedSourceCount: number;
}

function deriveSummaryState(r: HostScannerSummaryRow): {
  state: ReachabilityState;
  label: string;
} {
  if (r.total_sources === 0) {
    return { state: "unchecked", label: "No attached sources" };
  }
  if (r.reaches_count === r.total_sources) {
    return { state: "reachable", label: `Reaches all ${r.total_sources}` };
  }
  if (r.unreachable_count === r.total_sources) {
    return {
      state: "unreachable",
      label: `Reaches 0 of ${r.total_sources}`,
    };
  }
  if (r.reaches_count > 0) {
    // Mixed result — some succeeded, some never probed or failed.
    // Render as reachable; the per-source panel exposes the gory detail.
    return {
      state: "reachable",
      label: `Reaches ${r.reaches_count} of ${r.total_sources}`,
    };
  }
  return {
    state: "unchecked",
    label: `Not tested (${r.not_yet_probed_count} of ${r.total_sources} sources)`,
  };
}

/**
 * Host-level eligibility editor. One row per scanner, aggregating
 * "how many of this host's attached sources can the scanner reach".
 * "Apply" bulk-writes the chosen scanner set across every attached
 * source via PATCH /api/hosts/{id}/allowed-scanners. v0.5.7.
 */
export function HostAllowedScannersPanel({ hostId, attachedSourceCount }: Props) {
  const summaryQ = useHostScannerSummary(hostId);
  const update = useUpdateHostAllowedScanners();
  const testShares = useTestHostShares();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [original, setOriginal] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!summaryQ.data) return;
    // Initial state: scanners that are currently allowed on at least
    // one attached source. The host view's "selected" semantics is
    // "scanners we'd like to allow on every attached source".
    const allowed = new Set(
      summaryQ.data
        .filter((r) => r.currently_allowed_count > 0)
        .map((r) => r.scanner_id),
    );
    setSelected(allowed);
    setOriginal(allowed);
  }, [summaryQ.data]);

  const dirty = useMemo(() => {
    if (selected.size !== original.size) return true;
    for (const id of selected) if (!original.has(id)) return true;
    return false;
  }, [selected, original]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleTestReachability() {
    try {
      const res = await testShares.mutateAsync({ hostId });
      const sources = new Map<string, boolean>();
      for (const row of res.results) {
        const prev = sources.get(row.source_id);
        sources.set(row.source_id, prev === true || row.ok === true);
      }
      const reachable = Array.from(sources.values()).filter(Boolean).length;
      toast.success(
        `${reachable} of ${sources.size} share${sources.size === 1 ? "" : "s"} reachable from at least one scanner.`,
      );
    } catch (e) {
      toast.error(
        `Couldn't test reachability: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  async function handleApply() {
    try {
      const res = await update.mutateAsync({
        hostId,
        scannerIds: Array.from(selected),
      });
      toast.success(
        `Updated ${res.scanners_updated} scanner${res.scanners_updated === 1 ? "" : "s"} across ${res.sources_touched} attached source${res.sources_touched === 1 ? "" : "s"}.`,
      );
    } catch (e) {
      toast.error(
        `Couldn't apply scanner changes: ${e instanceof Error ? e.message : "unknown error"}.`,
      );
    }
  }

  if (attachedSourceCount === 0) {
    return (
      <p className="text-xs text-fg-muted italic">
        Add a share to this host first, then assign scanners.
      </p>
    );
  }
  if (summaryQ.isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs text-fg-muted">
        <Spinner /> Loading scanner summary…
      </div>
    );
  }
  if (summaryQ.isError) {
    return (
      <p className="text-xs text-rose-600">
        {summaryQ.error instanceof Error
          ? summaryQ.error.message
          : "Failed to load scanner summary"}
      </p>
    );
  }
  const rows = summaryQ.data ?? [];
  if (rows.length === 0) {
    return (
      <p className="text-xs text-fg-muted italic">
        No scanners registered yet.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-fg-muted">
        Reachability is what each scanner reports — credentialed access,
        not just network ping. Click <em>Test reachability</em> to ask
        every online scanner to authenticate and list each share.
      </p>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-fg-muted">
          {selected.size} of {rows.length} scanner{rows.length === 1 ? "" : "s"} selected
        </p>
        <Button
          size="sm"
          variant="ghost"
          onClick={handleTestReachability}
          loading={testShares.isPending}
          title="Probe every attached share against every online scanner."
        >
          Test reachability
        </Button>
      </div>

      <ul className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
        {rows.map((r) => {
          const s = deriveSummaryState(r);
          return (
            <li
              key={r.scanner_id}
              className="flex items-start gap-2 p-2 rounded-md border border-line"
            >
              <input
                type="checkbox"
                checked={selected.has(r.scanner_id)}
                onChange={() => toggle(r.scanner_id)}
                className="mt-1 h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-sm font-medium text-fg truncate">
                    {r.name}
                  </span>
                  {r.pool && (
                    <span className="text-[11px] text-fg-muted">
                      pool={r.pool}
                    </span>
                  )}
                  {!r.online && (
                    <span className="text-[11px] text-fg-muted">offline</span>
                  )}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-fg-muted">
                  <ReachabilityDot state={s.state} />
                  <span className="font-medium text-fg">{s.label}</span>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="flex items-center justify-end gap-2 pt-1">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setSelected(original)}
          disabled={!dirty || update.isPending}
        >
          Reset
        </Button>
        <Button
          size="sm"
          onClick={handleApply}
          loading={update.isPending}
          disabled={!dirty}
        >
          Apply to {attachedSourceCount} source{attachedSourceCount === 1 ? "" : "s"}
        </Button>
      </div>
    </div>
  );
}
