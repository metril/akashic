import { memo, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, Drawer, Spinner } from "../ui";
import { api } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import {
  useDeleteSource,
  useUpdateSource,
} from "../../hooks/useSources";
import type { Scan } from "../../types";
import { AllowedScannersPanel } from "./AllowedScannersPanel";
import { ProfilePicker } from "../credentials/ProfilePicker";
import {
  useCredentialProfiles,
  type CredentialProfileSummary,
} from "../../hooks/useCredentialProfiles";
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
  /** Latest pending/running scan id; null once the scan terminates.
   *  Used by DetailsTab to disable the Scan-now button while a scan
   *  is in flight. */
  activeScanId?: string | null;
  /** Most-recent scan id for this source — terminal-inclusive. Survives
   *  the scan completing so the Scan log tab can keep showing the log
   *  after the scan finishes. v0.29.7. */
  latestScanId?: string | null;
  /** True once the full source (GET /sources/{id}) has loaded. The
   *  list payload `source` is lean — it omits connection_config and
   *  credential_profile_id — so the edit form must wait for this
   *  before seeding its drafts, or it edits a phantom-empty config and
   *  a Save would wipe the source's credential profile. v0.31.3. */
  detailLoaded?: boolean;
}

type Tab = "details" | "history" | "live";

export const SourceDetail = memo(function SourceDetail({
  source, open, onClose, activeScanId, latestScanId, detailLoaded = false,
}: SourceDetailProps) {
  const [tab, setTab] = useState<Tab>("details");
  // v0.29.7 — drive the Scan log tab off the terminal-inclusive
  // scan id so the tab + content survive scan completion. The user
  // can review the just-completed scan's log without re-triggering.
  const scanLogScanId = latestScanId ?? activeScanId ?? null;

  // When the drawer opens for a different source, reset to the Details
  // tab. Otherwise the previous tab (e.g., History) leaks across opens.
  useEffect(() => {
    if (open) setTab("details");
  }, [source?.id, open]);

  // v0.29.7 — if the user is on the Scan log tab and the source has
  // no scan to inspect (never scanned, or just deleted history),
  // fall back to Details so the tab doesn't disappear underneath
  // them without a redirect.
  useEffect(() => {
    if (tab === "live" && !scanLogScanId) setTab("details");
  }, [tab, scanLogScanId]);

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
          {/* v0.29.7 — Scan log tab visible whenever the source has
              any scan to inspect (running OR terminal). Pre-fix the
              tab + content disappeared the instant a scan completed,
              leaving the user no way to review the just-finished
              scan's log. */}
          {scanLogScanId && (
            <TabButton active={tab === "live"} onClick={() => setTab("live")}>
              Scan log
            </TabButton>
          )}
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto pr-1">
          {tab === "details" && (
            detailLoaded ? (
              // DetailsTab mounts only once the full source has loaded,
              // so its draft state seeds from connection_config +
              // credential_profile_id rather than the lean list row.
              <DetailsTab
                source={source}
                onClose={onClose}
                activeScanId={activeScanId ?? null}
              />
            ) : (
              <div className="flex items-center justify-center py-16 text-fg-subtle">
                <Spinner />
              </div>
            )
          )}
          {tab === "history" && (
            <SourceAuditTab sourceId={source.id} visible={tab === "history"} />
          )}
          {tab === "live" && scanLogScanId && (
            <InlineLogPanel scanId={scanLogScanId} sourceName={source.name} />
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
      role="tab"
      aria-selected={active}
      className={`px-3 py-1.5 -mb-px border-b-2 transition-colors rounded-t-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 ${
        active
          ? "border-fg text-fg font-medium"
          : "border-transparent text-fg-muted hover:text-fg hover:bg-surface-muted/40"
      }`}
    >
      {children}
    </button>
  );
}

/**
 * Describe where a source's scan credentials come from, for the
 * credential UI. A host-backed source whose own `credential_profile_id`
 * is null inherits the host's credentials at scan time — leaving the
 * picker on its null option means "inherit from the host", not "define
 * them here". Returns the read-only summary plus the host-aware label
 * for the picker's null option (undefined for hostless sources, which
 * keep the default "Inline credentials" label). v0.31.4.
 */
function credentialSummary(
  source: Source,
  profiles: CredentialProfileSummary[] | undefined,
): { display: string; inheritLabel: string | undefined } {
  const nameOf = (id: string | null | undefined): string | null =>
    (id ? profiles?.find((p) => p.id === id)?.name : null) ?? null;

  const hostProfile = source.host
    ? nameOf(source.host.credential_profile_id)
    : null;
  const inheritLabel = source.host
    ? hostProfile
      ? `Inherit from host "${source.host.name}" — profile "${hostProfile}"`
      : `Inherit from host "${source.host.name}"`
    : undefined;

  const ownProfile = nameOf(source.credential_profile_id);
  let display: string;
  if (ownProfile) {
    display = `Profile "${ownProfile}"`;
  } else if (source.host) {
    display = hostProfile
      ? `Inherited from host "${source.host.name}" — profile "${hostProfile}"`
      : `Inherited from host "${source.host.name}"`;
  } else {
    display = "Inline (defined on this source)";
  }
  return { display, inheritLabel };
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
  // v0.31.4 — resolve where this source's credentials come from so the
  // credential UI is honest about host inheritance (a host-backed
  // source's own credential_profile_id stays null by design).
  const credentialProfiles = useCredentialProfiles(
    source.type as CredentialProfileSummary["type"],
  );
  const credInfo = credentialSummary(source, credentialProfiles.data);


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
  // v0.5.10 — credential profile is editable post-create. null means
  // "inline" (the existing connection_config carries credentials, or
  // the host's credentials apply if attached).
  const [draftCredentialProfileId, setDraftCredentialProfileId] =
    useState<string | null>(source.credential_profile_id ?? null);
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
    setDraftCredentialProfileId(source.credential_profile_id ?? null);
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

  // The connection-config validator must only gate saving when the
  // config was actually edited. `draftConfig` is seeded from the API's
  // response, which masks secrets as "***" — re-validating that
  // untouched, un-validatable config would (e.g. for OAuth sources)
  // return a false error and freeze the Save button, blocking edits to
  // unrelated fields like max_parallel_scanners. v0.31.1.
  const configChanged =
    JSON.stringify(draftConfig) !==
    JSON.stringify((source.connection_config ?? {}) as Partial<AnyConfig>);
  const blockingError = configChanged ? validationError : null;

  async function handleSave() {
    setError(null);
    if (blockingError) {
      setError(blockingError);
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
          credential_profile_id: draftCredentialProfileId,
        },
      });
      toast.promise(promise, {
        loading: "Saving source…",
        success: `Saved "${source.name}".`,
        error: (e: unknown) =>
          `Couldn't save source: ${e instanceof Error ? e.message : "unknown error"}.`,
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
      setDraftCredentialProfileId(updated.credential_profile_id ?? null);
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
        // Test with the same credentials a scan would use — the
        // profile's password / the host's config, not just the
        // (often password-less) inline share config.
        credential_profile_id: draftCredentialProfileId,
        host_id: source.host_id ?? null,
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
    // v0.28.0 — Removable / unreachable guard removed alongside the
    // continuous-poll subsystem. The cached is_reachable column is
    // gone and re-deriving freshness on every click would itself
    // require a network probe; if the source is offline at scan time,
    // the scan failure path surfaces it cleanly.
    const p = api.post("/scans/trigger", {
      source_id: source.id,
      scan_type: "incremental",
    });
    toast.promise(p, {
      loading: `Triggering scan of "${source.name}"…`,
      success: `Started scan of "${source.name}".`,
      error: (e: unknown) =>
        `Couldn't start scan: ${e instanceof Error ? e.message : "unknown error"}.`,
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
      loading: `Deleting "${source.name}"…`,
      success: purgeEntries
        ? `Deleted "${source.name}" and its indexed entries.`
        : `Deleted "${source.name}". Indexed entries kept.`,
      error: (e: unknown) =>
        `Couldn't delete source: ${e instanceof Error ? e.message : "unknown error"}.`,
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
        <DisplayRows source={source} credentialDisplay={credInfo.display} />
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
          credentialProfileId={draftCredentialProfileId}
          onCredentialProfileIdChange={setDraftCredentialProfileId}
          inheritLabel={credInfo.inheritLabel}
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
              reserveLabel="Scanning…"
            >
              {source.status === "scanning"
                ? "Scanning…"
                : activeScanId != null
                  ? "Queued…"
                  : "Scan now"}
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
              disabled={!!blockingError}
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

const DisplayRows = memo(function DisplayRows({
  source, credentialDisplay,
}: {
  source: Source;
  credentialDisplay: string;
}) {
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
  // v0.5.11 — surface inaccessible-counts from the most recent
  // completed scan inline with "Last scanned" so users can tell when
  // a scan was incomplete (perm denied, ENOENT) vs. clean. Cheap
  // single-row fetch keyed on source id.
  const lastScanQuery = useQuery<Scan[]>({
    queryKey: ["scans", "last-completed", source.id],
    queryFn: () =>
      api.get<Scan[]>(
        `/scans?source_id=${source.id}&status=completed&limit=1`,
      ),
    staleTime: 30_000,
  });
  const lastScan = lastScanQuery.data?.[0] ?? null;
  const inaccessibleDirs = lastScan?.inaccessible_dirs ?? 0;
  const inaccessibleFiles = lastScan?.inaccessible_files ?? 0;
  const inaccessibleTotal = inaccessibleDirs + inaccessibleFiles;
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
            className="text-accent-600 hover:underline font-medium"
          >
            {source.host.name}
          </Link>
          <span className="text-xs text-fg-muted ml-2">
            (edit credentials on the Hosts page)
          </span>
        </Row>
      )}
      {/* v0.31.4 — effective credential source. A host-backed source
          with no source-level profile inherits the host's credentials;
          show that plainly rather than implying inline credentials. */}
      {source.type !== "local" && (
        <Row label="Credentials">
          <span className="text-fg-muted">{credentialDisplay}</span>
        </Row>
      )}
      <Row label="Reachability">
        <ReachabilityBadge sourceId={source.id} />
      </Row>
      <Row label="Last scanned">
        <div className="text-fg-muted">
          <div>{lastScanStr}</div>
          {inaccessibleTotal > 0 && (
            <div
              className="text-xs text-amber-700 dark:text-amber-300 mt-0.5"
              title="The scanner couldn't enter these entries — usually permission-denied or files removed mid-scan. The scan completed but the affected subtrees are missing."
            >
              {inaccessibleTotal.toLocaleString()} inaccessible item
              {inaccessibleTotal !== 1 ? "s" : ""} skipped
              {inaccessibleDirs > 0 && inaccessibleFiles > 0
                ? ` (${inaccessibleDirs} dir${inaccessibleDirs !== 1 ? "s" : ""}, ${inaccessibleFiles} file${inaccessibleFiles !== 1 ? "s" : ""})`
                : ""}
            </div>
          )}
        </div>
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
  credentialProfileId: string | null;
  onCredentialProfileIdChange: (id: string | null) => void;
  // Host-aware label for the credential picker's null option — set
  // when the source belongs to a host, so "Inline" reads as "Inherit
  // from host …" instead. Undefined for hostless sources. v0.31.4.
  inheritLabel?: string;
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
  credentialProfileId,
  onCredentialProfileIdChange,
  inheritLabel,
}: EditRowsProps) {
  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs font-medium text-fg mb-1">Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          className="w-full rounded-md border border-line px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent-400 focus:border-accent-400"
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
      {type !== "local" && (
        <ProfilePicker
          type={type as "smb" | "nfs" | "s3"}
          value={credentialProfileId}
          onChange={onCredentialProfileIdChange}
          label="Credentials"
          inheritLabel={inheritLabel}
          hint={
            credentialProfileId
              ? "Using credentials from this profile. Inline keys on this share still override."
              : inheritLabel
                ? "This source inherits its credentials from the host. Pick a profile to override them for this source only."
                : "Pick a saved profile, or leave on Inline to use the credentials defined in this form."
          }
        />
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
          className="w-full rounded-md border border-line px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-accent-400 focus:border-accent-400"
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
          className="w-full rounded-md border border-line px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent-400 focus:border-accent-400"
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
          className="mt-0.5 h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
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
