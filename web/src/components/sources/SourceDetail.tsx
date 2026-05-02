import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Drawer } from "../ui";
import { api } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import { useUpdateSource, useDeleteSource } from "../../hooks/useSources";
import { DeleteSourceModal } from "./DeleteSourceModal";
import { RecoverOrphansModal } from "./RecoverOrphansModal";
import { useOrphanMatchCount } from "../../hooks/useOrphanRecovery";
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

interface SourceDetailProps {
  source: Source | null;
  open: boolean;
  onClose: () => void;
  /** Latest scan id for this source, when source.status === "scanning" */
  activeScanId?: string | null;
}

type Tab = "details" | "history" | "live";

export function SourceDetail({ source, open, onClose, activeScanId }: SourceDetailProps) {
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
          {tab === "details" && <DetailsTab source={source} onClose={onClose} />}
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
}

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
          ? "border-gray-900 text-fg font-medium"
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
}

function DetailsTab({ source, onClose }: DetailsTabProps) {
  const queryClient = useQueryClient();
  const { isAdmin } = useAuth();
  const updateSource = useUpdateSource();
  const deleteSource = useDeleteSource();
  const testSource = useTestSource();

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(source.name);
  const [draftConfig, setDraftConfig] = useState<Partial<AnyConfig>>(
    (source.connection_config ?? {}) as Partial<AnyConfig>,
  );
  const [draftSchedule, setDraftSchedule] = useState<string>(source.scan_schedule ?? "");
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestSourceResult | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [recoverOpen, setRecoverOpen] = useState(false);

  // v0.4.0 — surface orphaned-entries-that-match-this-source as a
  // banner with a recover affordance. Cheap COUNT under the hood.
  const orphanCountQ = useOrphanMatchCount(isAdmin ? source.id : null);
  const orphanCount = orphanCountQ.data?.count ?? 0;

  // When `source` changes (drawer reopened with a different row), reset
  // edit state.
  useEffect(() => {
    setEditing(false);
    setDraftName(source.name);
    setDraftConfig((source.connection_config ?? {}) as Partial<AnyConfig>);
    setDraftSchedule(source.scan_schedule ?? "");
    setError(null);
    setTestResult(null);
  }, [source.id]);

  const validationError = validateSourceConfig(source.type as SourceType, draftConfig);

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

  async function handleScanNow() {
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
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["scans", "active"] });
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
          name={draftName}
          onNameChange={setDraftName}
          config={draftConfig}
          onConfigChange={setDraftConfig}
          schedule={draftSchedule}
          onScheduleChange={setDraftSchedule}
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

      {isAdmin && orphanCount > 0 && (
        <div className="rounded-md p-3 text-xs bg-blue-50 dark:bg-blue-500/10 border border-blue-200 dark:border-blue-500/30 flex items-center justify-between gap-3">
          <span className="text-fg">
            <strong>{orphanCount.toLocaleString()}</strong> orphaned
            file{orphanCount === 1 ? "" : "s"} match this source's
            tree. Re-attach them to keep their tags + history.
          </span>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setRecoverOpen(true)}
          >
            Preview & recover
          </Button>
        </div>
      )}

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
              disabled={source.status === "scanning"}
            >
              {source.status === "scanning" ? "Scanning…" : "Scan now"}
            </Button>
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
}

function DisplayRows({ source }: { source: Source }) {
  const cfg = (source.connection_config ?? {}) as Record<string, unknown>;
  const summary = formatSourceSummary(source);

  // Show every config field, with secrets rendered as a state token
  // rather than the raw `"***"` (less alarming than a literal *** in
  // the UI).
  const fieldRows = Object.entries(cfg);

  return (
    <dl className="text-sm space-y-2">
      <Row label="Summary"><span className="font-mono text-xs">{summary}</span></Row>
      <Row label="Status"><span>{source.status}</span></Row>
      <Row label="Last scan">
        <span className="text-fg-muted">{formatDateTime(source.last_scan_at)}</span>
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
          {fieldRows.map(([k, v]) => (
            <Row key={k} label={k}>
              {v === "***" ? (
                <span className="text-xs text-fg-muted italic">(set, masked)</span>
              ) : (
                <span className="font-mono text-xs break-all">
                  {typeof v === "string" ? v : JSON.stringify(v)}
                </span>
              )}
            </Row>
          ))}
        </dl>
      </div>
    </dl>
  );
}

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
  name: string;
  onNameChange: (s: string) => void;
  config: Partial<AnyConfig>;
  onConfigChange: (c: Partial<AnyConfig>) => void;
  schedule: string;
  onScheduleChange: (s: string) => void;
}

function EditRows({
  type,
  name,
  onNameChange,
  config,
  onConfigChange,
  schedule,
  onScheduleChange,
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
      <SourceFieldSet type={type} value={config} onChange={onConfigChange} />
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
