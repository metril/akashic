import { memo, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Drawer } from "../ui";
import { api } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import {
  useCheckSourceReachability,
  useDeleteSource,
  useUpdateSource,
} from "../../hooks/useSources";
import { AllowedScannersPanel } from "./AllowedScannersPanel";
import { DeleteSourceModal } from "./DeleteSourceModal";
import { ReachabilityBadge } from "./ReachabilityBadge";
import { RecoverOrphansModal } from "./RecoverOrphansModal";
import { useTestSource, type TestSourceResult } from "../../hooks/useTestSource";
import { useQueryClient } from "@tanstack/react-query";
import type { Source } from "../../types";
import { formatDateTime } from "../../lib/format";
import { formatSourceSummary } from "../../lib/sources";
import { SourceFieldSet } from "./SourceFieldSet";
import { SourceAuditTab } from "./SourceAuditTab";
import { ScanLogPanel } from "../scans/ScanLogPanel";
import type { AnyConfig, SourceType } from "./sourceTypes";
import { validateSourceConfig } from "./sourceTypes";
import {
  ShareFields,
  type ShareConfig,
  validateShareConfig,
} from "./source-fields/ShareFields";
import { Link } from "react-router-dom";

interface SourceDetailProps {
  source: Source | null;
  open: boolean;
  onClose: () => void;
  /** Latest scan id for this source, when source.status === "scanning" */
  activeScanId?: string | null;
}

type Tab = "details" | "history" | "live";

export const SourceDetail = memo(function SourceDetail({
  source, open, onClose, activeScanId,
}: SourceDetailProps) {
  const [tab, setTab] = useState<Tab>("details");
  const isScanning = source?.status === "scanning";

  // When the drawer opens for a different source, reset to the Details
  // tab. Otherwise the previous tab (e.g., History) leaks across opens.
  useEffect(() => {
    if (open) setTab("details");
  }, [source?.id, open]);

  if (!source) return null;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="lg"
      title={
        <div className="flex items-center gap-2">
          <span>{source.name}</span>
          <Badge variant="neutral">{source.type}</Badge>
        </div>
      }
    >
      <div className="flex flex-col h-full px-6 py-5">
        {/* Tabs */}
        <div className="flex border-b border-line mb-3 text-sm shrink-0">
          <TabButton active={tab === "details"} onClick={() => setTab("details")}>
            Details
          </TabButton>
          <TabButton active={tab === "history"} onClick={() => setTab("history")}>
            History
          </TabButton>
          {isScanning && (
            <TabButton active={tab === "live"} onClick={() => setTab("live")}>
              Live log
            </TabButton>
          )}
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto pr-1">
          {tab === "details" && (
            <DetailsTab
              source={source}
              onClose={onClose}
              activeScanId={activeScanId ?? null}
            />
          )}
          {tab === "history" && (
            <SourceAuditTab sourceId={source.id} visible={tab === "history"} />
          )}
          {tab === "live" && isScanning && activeScanId && (
            <InlineLogPanel scanId={activeScanId} sourceName={source.name} />
          )}
        </div>
      </div>
    </Drawer>
  );
});

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 -mb-px border-b-2 ${
        active
          ? "border-fg text-fg font-medium"
          : "border-transparent text-fg-muted hover:text-fg"
      }`}
    >
      {children}
    </button>
  );
}

interface DetailsTabProps {
  source: Source;
  onClose: () => void;
  /** Latest pending/running scan id for this source, or null. Drives
   *  the "Queued…" / "Scanning…" button state so a re-press during the
   *  agent-lease window doesn't look like a no-op (v0.4.4). */
  activeScanId: string | null;
}

const DetailsTab = memo(function DetailsTab({
  source, onClose, activeScanId,
}: DetailsTabProps) {
  const queryClient = useQueryClient();
  const { isAdmin } = useAuth();
  const updateSource = useUpdateSource();
  const deleteSource = useDeleteSource();
  const testSource = useTestSource();
  const checkReachability = useCheckSourceReachability();

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(source.name);
  const [draftConfig, setDraftConfig] = useState<Partial<AnyConfig>>(
    (source.connection_config ?? {}) as Partial<AnyConfig>,
  );
  const [draftSchedule, setDraftSchedule] = useState<string>(source.scan_schedule ?? "");
  const [draftIsRemovable, setDraftIsRemovable] = useState<boolean>(source.is_removable);
  const [draftMaxParallelScanners, setDraftMaxParallelScanners] = useState<number>(
    source.max_parallel_scanners ?? 1,
  );
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestSourceResult | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // v0.4.3 — Recover orphans is now an explicit action under the
  // panel's Advanced section, not a banner. The proactive banner
  // fired a JOIN-heavy COUNT on every panel open even though
  // most users never delete-with-preserve. Click-to-open is the
  // right intent gate.
  const [recoverOpen, setRecoverOpen] = useState(false);

  // When `source` changes (drawer reopened with a different row), reset
  // edit state.
  useEffect(() => {
    setEditing(false);
    setDraftName(source.name);
    setDraftConfig((source.connection_config ?? {}) as Partial<AnyConfig>);
    setDraftSchedule(source.scan_schedule ?? "");
    setDraftIsRemovable(source.is_removable);
    setDraftMaxParallelScanners(source.max_parallel_scanners ?? 1);
    setError(null);
    setTestResult(null);
  }, [source.id]);

  // Two validation paths: when a Host owns the connection-level
  // config, only the share-only fields need to validate; otherwise
  // the full legacy SourceFieldSet validator applies.
  const hasHost = source.host_id != null;
  const validationError = hasHost
    ? validateShareConfig(source.type as SourceType, draftConfig as ShareConfig)
    : validateSourceConfig(source.type as SourceType, draftConfig);

  async function handleSave() {
    setError(null);
    if (validationError) {
      setError(validationError);
      return;
    }
    // Strip any `"***"` values still present in secret-named fields —
    // they signal "user didn't retype, leave existing alone." The
    // backend's secret-merge will preserve the real secret regardless,
    // but stripping client-side keeps the wire payload clean.
    const cleaned: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(draftConfig)) {
      if (v === "***") continue;
      cleaned[k] = v;
    }
    try {
      const promise = updateSource.mutateAsync({
        id: source.id,
        data: {
          name: draftName,
          connection_config: cleaned,
          scan_schedule: draftSchedule || null,
          is_removable: draftIsRemovable,
          max_parallel_scanners: draftMaxParallelScanners,
        },
      });
      toast.promise(promise, {
        loading: "Saving…",
        success: "Source updated.",
        error: (e: unknown) =>
          `Save failed: ${e instanceof Error ? e.message : "unknown error"}`,
      });
      const updated = await promise;
      // Seed local draft state from the PATCH response (the latest
      // server state with secrets re-masked) so a subsequent
      // Edit→Cancel doesn't roll back to the now-stale `source` prop
      // that react-query hasn't refetched yet.
      setDraftName(updated.name);
      setDraftConfig((updated.connection_config ?? {}) as Partial<AnyConfig>);
      setDraftSchedule(updated.scan_schedule ?? "");
      setDraftIsRemovable(updated.is_removable);
      setDraftMaxParallelScanners(updated.max_parallel_scanners ?? 1);
      queryClient.invalidateQueries({ queryKey: ["sources", source.id, "audit"] });
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function handleTest() {
    setTestResult(null);
    try {
      const cleaned: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(draftConfig)) {
        if (v === "***") continue;
        cleaned[k] = v;
      }
      const r = await testSource.mutateAsync({
        type: source.type as SourceType,
        connection_config: cleaned,
      });
      setTestResult(r);
    } catch (e) {
      setTestResult({
        ok: false,
        step: null,
        error: e instanceof Error ? e.message : "Test failed",
      });
    }
  }

  async function handleCheckNow() {
    const p = checkReachability.mutateAsync(source.id);
    toast.promise(p, {
      loading: "Checking reachability…",
      success: (r) =>
        r.result.ok
          ? r.result.tier
            ? `Reachable · ${r.result.tier}`
            : "Reachable."
          : `Unreachable: ${r.result.step ?? "error"}: ${r.result.error ?? "unknown"}`,
      error: (e: unknown) =>
        `Check failed: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      await p;
    } catch {
      // toast already surfaced the error
    }
  }

  async function handleScanNow() {
    // Removable + known-unreachable → block to avoid queuing a scan
    // that will immediately fail at Connect time. The user can still
    // Check now to refresh state, then retry.
    if (source.is_removable && source.is_reachable === false) {
      toast.error(
        "Source is currently unmounted. Click Check now to refresh, or reconnect the drive first.",
      );
      return;
    }
    const p = api.post("/scans/trigger", {
      source_id: source.id,
      scan_type: "incremental",
    });
    toast.promise(p, {
      loading: "Triggering scan…",
      success: "Scan started.",
      error: (e: unknown) =>
        `Couldn't start scan: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      await p;
      // v0.4.7: removed invalidateQueries(["sources"]) +
      // (["scans","active"]) — the trigger's WS scan.state event
      // already updates the singleton store and the reconciler
      // patches the React Query caches when source_status flips.
      // The invalidate refetched /api/sources for nothing, returning
      // a fresh array reference that fails React.memo shallow
      // equality on every visible card and re-fired
      // BucketSecurityCard's JSON.stringify on every S3 card.
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to trigger scan");
    }
  }

  async function handleDeleteConfirmed({ purgeEntries }: { purgeEntries: boolean }) {
    const p = deleteSource.mutateAsync({ id: source.id, purgeEntries });
    toast.promise(p, {
      loading: "Deleting source…",
      success: purgeEntries
        ? `Deleted "${source.name}" and its indexed entries.`
        : `Deleted "${source.name}". Indexed entries kept.`,
      error: (e: unknown) =>
        `Delete failed: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      await p;
      setConfirmDelete(false);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  return (
    <div className="space-y-4">
      {!editing ? (
        <DisplayRows source={source} />
      ) : (
        <EditRows
          type={source.type as SourceType}
          hasHost={hasHost}
          name={draftName}
          onNameChange={setDraftName}
          config={draftConfig}
          onConfigChange={setDraftConfig}
          schedule={draftSchedule}
          onScheduleChange={setDraftSchedule}
          isRemovable={draftIsRemovable}
          onIsRemovableChange={setDraftIsRemovable}
          maxParallelScanners={draftMaxParallelScanners}
          onMaxParallelScannersChange={setDraftMaxParallelScanners}
        />
      )}

      {testResult && (
        <div
          className={`rounded-md p-2 text-xs ${
            testResult.ok
              ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300"
              : "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300"
          }`}
        >
          {testResult.ok
            ? "Connection OK"
            : `${testResult.step ?? "error"}: ${testResult.error ?? "unknown"}`}
        </div>
      )}

      {error && <p className="text-xs text-rose-600">{error}</p>}

      <div className="flex flex-wrap gap-2 pt-2 border-t border-line-subtle">
        {!editing ? (
          <>
            {isAdmin && (
              <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
                Edit
              </Button>
            )}
            <Button
              size="sm"
              variant="secondary"
              onClick={handleScanNow}
              // v0.4.4: also disable while a scan is QUEUED (pending,
              // not yet picked up by an agent). Without this the
              // button stays enabled for ~5s between trigger and the
              // agent's lease poll, and users press it twice
              // assuming the first click did nothing. The api now
              // dedups on the server side too — both belt-and-braces.
              disabled={source.status === "scanning" || activeScanId != null}
              title={
                source.is_removable && source.is_reachable === false
                  ? "Source is currently unmounted. Use Check now first."
                  : undefined
              }
            >
              {source.status === "scanning"
                ? "Scanning…"
                : activeScanId != null
                  ? "Queued…"
                  : "Scan now"}
            </Button>
            {source.is_removable && (
              <Button
                size="sm"
                variant="secondary"
                onClick={handleCheckNow}
                loading={checkReachability.isPending}
                title="Run a connection probe and update the reachability badge."
              >
                Check now
              </Button>
            )}
            {isAdmin && (
              <Button
                size="sm"
                variant="danger"
                onClick={() => setConfirmDelete(true)}
                loading={deleteSource.isPending}
              >
                Delete
              </Button>
            )}
            {isAdmin && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setRecoverOpen(true)}
                title="Re-attach indexed entries from a previously deleted source whose paths match this source's tree"
              >
                Recover orphans…
              </Button>
            )}
            {!isAdmin && (
              <p className="text-xs text-fg-muted italic w-full mt-1">
                Read-only — admin permission required to edit or delete.
              </p>
            )}
          </>
        ) : (
          <>
            <Button
              size="sm"
              onClick={handleSave}
              loading={updateSource.isPending}
              disabled={!!validationError}
            >
              Save
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={handleTest}
              loading={testSource.isPending}
              disabled={!!validationError}
              title={validationError ?? undefined}
            >
              Test connection
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setDraftName(source.name);
                setDraftConfig((source.connection_config ?? {}) as Partial<AnyConfig>);
                setDraftSchedule(source.scan_schedule ?? "");
                setDraftIsRemovable(source.is_removable);
                setDraftMaxParallelScanners(source.max_parallel_scanners ?? 1);
                setError(null);
                setTestResult(null);
              }}
            >
              Cancel
            </Button>
          </>
        )}
      </div>

      <RecoverOrphansModal
        open={recoverOpen}
        sourceId={source.id}
        sourceName={source.name}
        onClose={() => setRecoverOpen(false)}
      />
      <DeleteSourceModal
        open={confirmDelete}
        sourceId={source.id}
        sourceName={source.name}
        loading={deleteSource.isPending}
        onConfirm={handleDeleteConfirmed}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
});

interface DisplayFieldRow {
  key: string;
  isMasked: boolean;
  display: string;
}

const DisplayRows = memo(function DisplayRows({ source }: { source: Source }) {
  // v0.4.5: lift expensive derivations into useMemo so a parent
  // re-render that doesn't actually mutate `source` (e.g. anything
  // up the React tree commits) doesn't pay JSON.stringify per
  // config field per render. The whole panel is React.memo'd at the
  // outer level too, so DisplayRows only re-renders when source
  // identity changes anyway — but the memo is the second line of
  // defense if a future caller forgets that.
  const summary = useMemo(() => formatSourceSummary(source), [source]);
  const lastScanStr = useMemo(
    () => formatDateTime(source.last_scan_at),
    [source.last_scan_at],
  );
  const fieldRows = useMemo<DisplayFieldRow[]>(() => {
    const cfg = (source.connection_config ?? {}) as Record<string, unknown>;
    return Object.entries(cfg).map(([k, v]) => ({
      key: k,
      isMasked: v === "***",
      display: typeof v === "string" ? v : JSON.stringify(v),
    }));
  }, [source.connection_config]);

  return (
    <dl className="text-sm space-y-2">
      <Row label="Summary"><span className="font-mono text-xs">{summary}</span></Row>
      {/* Status row dropped in v0.5.9 — the SourcePill on the parent
          card carries this, and the active-scan banner above shows
          in-flight state. The legacy "online"/"offline" string here
          duplicated the reachability badge below. */}
      {source.host && (
        <Row label="Host">
          <Link
            to="/hosts"
            className="text-blue-600 hover:underline font-medium"
          >
            {source.host.name}
          </Link>
          <span className="text-xs text-fg-muted ml-2">
            (edit credentials on the Hosts page)
          </span>
        </Row>
      )}
      <Row label="Reachability">
        <ReachabilityBadge source={source} />
      </Row>
      <Row label="Last scanned">
        <span className="text-fg-muted">{lastScanStr}</span>
      </Row>
      {source.scan_schedule && (
        <Row label="Schedule">
          <span className="font-mono text-xs">{source.scan_schedule}</span>
        </Row>
      )}
      <div className="pt-2 border-t border-line-subtle">
        <p className="text-xs uppercase tracking-wide text-fg-subtle mb-2">
          Connection config
        </p>
        <dl className="space-y-1">
          {fieldRows.length === 0 && (
            <p className="text-xs text-fg-muted italic">(empty)</p>
          )}
          {fieldRows.map((row) => (
            <Row key={row.key} label={row.key}>
              {row.isMasked ? (
                <span className="text-xs text-fg-muted italic">(set, masked)</span>
              ) : (
                <span className="font-mono text-xs break-all">{row.display}</span>
              )}
            </Row>
          ))}
        </dl>
      </div>
      <div className="pt-3 border-t border-line-subtle">
        <p className="text-xs uppercase tracking-wide text-fg-subtle mb-2">
          Allowed scanners
        </p>
        <AllowedScannersPanel sourceId={source.id} />
      </div>
    </dl>
  );
});

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 text-sm">
      <dt className="shrink-0 w-32 text-xs uppercase tracking-wide text-fg-subtle pt-0.5">
        {label}
      </dt>
      <dd className="flex-1 min-w-0">{children}</dd>
    </div>
  );
}

interface EditRowsProps {
  type: SourceType;
  // True when the source belongs to a Host — connection_config holds
  // only share-shaped fields; render the lean ShareFields editor and
  // direct host edits to /hosts.
  hasHost: boolean;
  name: string;
  onNameChange: (s: string) => void;
  config: Partial<AnyConfig>;
  onConfigChange: (c: Partial<AnyConfig>) => void;
  schedule: string;
  onScheduleChange: (s: string) => void;
  isRemovable: boolean;
  onIsRemovableChange: (v: boolean) => void;
  maxParallelScanners: number;
  onMaxParallelScannersChange: (v: number) => void;
}

function EditRows({
  type,
  hasHost,
  name,
  onNameChange,
  config,
  onConfigChange,
  schedule,
  onScheduleChange,
  isRemovable,
  onIsRemovableChange,
  maxParallelScanners,
  onMaxParallelScannersChange,
}: EditRowsProps) {
  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs font-medium text-fg mb-1">Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          className="w-full rounded-md border border-line px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
        />
      </div>
      <div className="rounded-md bg-amber-50 border border-amber-100 px-2.5 py-1.5">
        <p className="text-xs text-amber-800">
          Source type cannot be changed. Delete and re-create with the new type.
        </p>
      </div>
      {hasHost ? (
        <ShareFields
          type={type}
          value={config as ShareConfig}
          onChange={onConfigChange as (c: ShareConfig) => void}
        />
      ) : (
        <SourceFieldSet type={type} value={config} onChange={onConfigChange} />
      )}
      <div>
        <label className="block text-xs font-medium text-fg mb-1">
          Scan schedule (cron, optional)
        </label>
        <input
          type="text"
          value={schedule}
          onChange={(e) => onScheduleChange(e.target.value)}
          placeholder="0 2 * * *"
          className="w-full rounded-md border border-line px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-fg mb-1">
          Max parallel scanners
        </label>
        <input
          type="number"
          min={1}
          max={16}
          value={maxParallelScanners}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            if (Number.isFinite(n) && n >= 1 && n <= 16) {
              onMaxParallelScannersChange(n);
            }
          }}
          className="w-full rounded-md border border-line px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
        />
        <p className="text-[11px] text-fg-muted mt-1">
          Cap (1–16) on cooperating scanners per scan. Default 1
          preserves the legacy single-scanner walk.
        </p>
      </div>
      <label className="flex items-start gap-2 text-sm cursor-pointer select-none">
        <input
          type="checkbox"
          checked={isRemovable}
          onChange={(e) => onIsRemovableChange(e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-line text-blue-600 focus:ring-blue-400"
        />
        <span>
          <span className="font-medium text-fg">Intermittently available</span>
          <span className="block text-xs text-fg-muted mt-0.5">
            External / removable storage. Surfaces a reachable / unmounted
            indicator and a Check-now button; keeps Scan-now from queuing
            doomed scans against an unplugged drive.
          </span>
        </span>
      </label>
    </div>
  );
}

function InlineLogPanel({ scanId, sourceName }: { scanId: string; sourceName: string }) {
  // Reuse the existing ScanLogPanel as a child drawer would feel weird
  // (drawer-on-drawer). Instead render the panel content directly here.
  // The simplest implementation: open a child drawer with the same
  // component. UX-wise that's fine — the parent stays underneath.
  const [open, setOpen] = useState(true);
  return (
    <>
      <p className="text-sm text-fg-muted mb-3">
        Live scan output for{" "}
        <span className="font-medium">{sourceName}</span>:
      </p>
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
        Re-open log panel
      </Button>
      <ScanLogPanel
        open={open}
        onClose={() => setOpen(false)}
        scanId={scanId}
        sourceName={sourceName}
      />
    </>
  );
}
