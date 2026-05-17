import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  Input,
  Page,
  SectionState,
  StatCard,
} from "../components/ui";
import type { BadgeVariant } from "../components/ui";

// — wire types (mirror api/akashic/routers/maintenance.py) —

interface Overview {
  scans_by_status: Record<string, number>;
  scans_active: number;
  entries_total: number;
  sources_total: number;
  scanners_total: number;
  scanners_online: number;
  scan_log_rows: number;
  scan_log_purgeable: number;
  log_retention_days: number;
  meili_documents: number | null;
}

interface StuckScan {
  scan_id: string;
  source_id: string | null;
  source_name: string | null;
  status: string;
  scan_type: string;
  started_at: string | null;
  last_heartbeat_at: string | null;
  age_seconds: number | null;
  assigned_scanner_id: string | null;
  assigned_scanner_name: string | null;
}

interface MaintJob {
  id: string;
  kind: string;
  status: "running" | "succeeded" | "failed";
  params: Record<string, unknown>;
  result: { rows_affected?: number } | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

interface ScannerRow {
  id: string;
  name: string;
  hostname: string | null;
  version: string | null;
  last_seen_at: string | null;
  online: boolean;
  enabled: boolean;
}

const JOB_KINDS: { kind: string; label: string; description: string }[] = [
  {
    kind: "reindex_search",
    label: "Reindex search",
    description: "Rebuild every file's Meilisearch document from the database.",
  },
  {
    kind: "backfill_subtree_sizes",
    label: "Backfill folder sizes",
    description: "Recompute directory size and file/dir count aggregates.",
  },
  {
    kind: "backfill_viewable",
    label: "Backfill viewable flags",
    description: "Recompute the denormalized ACL projection on every entry.",
  },
  {
    kind: "warm_groups",
    label: "Warm group cache",
    description: "Pre-resolve group memberships for every identity binding.",
  },
];

// — helpers —

function rel(iso: string | null): string {
  if (!iso) return "—";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function dur(seconds: number | null): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function scanBadge(status: string): BadgeVariant {
  if (status === "running") return "scanning";
  if (status === "completed") return "online";
  if (status === "failed") return "failed";
  return "neutral";
}

function jobBadge(status: MaintJob["status"]): BadgeVariant {
  if (status === "running") return "scanning";
  if (status === "succeeded") return "online";
  return "failed";
}

/** Numeric-aware version compare ("v0.31.0" > "v0.30.2"). Returns the
 *  newest version string, or null when none can be compared. */
function newestVersion(versions: (string | null)[]): string | null {
  const parse = (v: string) =>
    v.replace(/^v/, "").split(/[.\-+]/).map((p) => parseInt(p, 10) || 0);
  let best: string | null = null;
  for (const v of versions) {
    if (!v) continue;
    if (best === null) {
      best = v;
      continue;
    }
    const a = parse(v);
    const b = parse(best);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const d = (a[i] ?? 0) - (b[i] ?? 0);
      if (d > 0) { best = v; break; }
      if (d < 0) break;
    }
  }
  return best;
}

type ConfirmState = {
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
};

export default function AdminMaintenance() {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [purgeDays, setPurgeDays] = useState("7");

  const overviewQ = useQuery<Overview>({
    queryKey: ["maintenance", "overview"],
    queryFn: () => api.get<Overview>("/admin/maintenance/overview"),
  });
  const stuckQ = useQuery<StuckScan[]>({
    queryKey: ["maintenance", "stuck"],
    queryFn: () => api.get<StuckScan[]>("/admin/maintenance/scans/stuck"),
  });
  const jobsQ = useQuery<MaintJob[]>({
    queryKey: ["maintenance", "jobs"],
    queryFn: () => api.get<MaintJob[]>("/admin/maintenance/jobs"),
    // Poll only while a job is in flight.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((j) => j.status === "running") ? 2500 : false,
  });
  const scannersQ = useQuery<ScannerRow[]>({
    queryKey: ["scanners"],
    queryFn: () => api.get<ScannerRow[]>("/scanners"),
    refetchInterval: 15_000,
  });

  function refreshScanState() {
    qc.invalidateQueries({ queryKey: ["maintenance", "overview"] });
    qc.invalidateQueries({ queryKey: ["maintenance", "stuck"] });
  }

  const cancelMut = useMutation<{ status: string }, Error, string>({
    mutationFn: (scanId) =>
      api.post<{ status: string }>(`/admin/maintenance/scans/${scanId}/cancel`),
    onSuccess: (r) => {
      toast.success(`Scan ${r.status}`);
      refreshScanState();
    },
    onError: (e) => toast.error(e.message || "Cancel failed"),
  });

  const watchdogMut = useMutation<
    { active_before: number; active_after: number },
    Error,
    void
  >({
    mutationFn: () =>
      api.post<{ active_before: number; active_after: number }>(
        "/admin/maintenance/watchdog/run",
      ),
    onSuccess: (r) => {
      const cleared = r.active_before - r.active_after;
      toast.success(
        cleared > 0
          ? `Watchdog cleared ${cleared} stuck scan${cleared === 1 ? "" : "s"}`
          : "Watchdog ran — no stuck scans",
      );
      refreshScanState();
    },
    onError: (e) => toast.error(e.message || "Watchdog failed"),
  });

  const purgeMut = useMutation<{ deleted: number }, Error, number>({
    mutationFn: (days) =>
      api.post<{ deleted: number }>("/admin/maintenance/logs/purge", {
        older_than_days: days,
      }),
    onSuccess: (r) => {
      toast.success(`Deleted ${r.deleted.toLocaleString()} log rows`);
      qc.invalidateQueries({ queryKey: ["maintenance", "overview"] });
    },
    onError: (e) => toast.error(e.message || "Purge failed"),
  });

  const jobMut = useMutation<MaintJob, Error, string>({
    mutationFn: (kind) =>
      api.post<MaintJob>("/admin/maintenance/jobs", { kind, params: {} }),
    onSuccess: (job) => {
      toast.success(`${jobLabel(job.kind)} started`);
      qc.invalidateQueries({ queryKey: ["maintenance", "jobs"] });
    },
    onError: (e) => toast.error(e.message || "Could not start job"),
  });

  const ov = overviewQ.data;
  const jobs = jobsQ.data ?? [];
  const runningKinds = new Set(
    jobs.filter((j) => j.status === "running").map((j) => j.kind),
  );
  const newest = newestVersion((scannersQ.data ?? []).map((s) => s.version));

  return (
    <Page
      title="Maintenance"
      description="Operational tooling — scan & log hygiene, search indexing, and scanner diagnostics."
      width="wide"
    >
      <div className="space-y-6">
        {/* — Overview — */}
        <section>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatCard
              label="Active scans"
              value={ov?.scans_active ?? 0}
              loading={overviewQ.isLoading}
              subtext="pending + running"
            />
            <StatCard
              label="Indexed entries"
              value={(ov?.entries_total ?? 0).toLocaleString()}
              loading={overviewQ.isLoading}
              subtext={
                ov?.meili_documents != null
                  ? `${ov.meili_documents.toLocaleString()} in search index`
                  : "search index unavailable"
              }
            />
            <StatCard
              label="Scan-log rows"
              value={(ov?.scan_log_rows ?? 0).toLocaleString()}
              loading={overviewQ.isLoading}
              subtext={`${(ov?.scan_log_purgeable ?? 0).toLocaleString()} purgeable`}
            />
            <StatCard
              label="Scanners online"
              value={`${ov?.scanners_online ?? 0} / ${ov?.scanners_total ?? 0}`}
              loading={overviewQ.isLoading}
              subtext={
                <Link to="/admin/system-status" className="text-accent-600 hover:underline">
                  Service status →
                </Link>
              }
            />
          </div>
        </section>

        {/* — Stuck scans — */}
        <Card padding="none">
          <div className="px-4 pt-4">
            <CardHeader
              title="Stuck scans"
              description="Scans left pending or running after a scanner crash. Cancelling one frees its source for a re-run."
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  loading={watchdogMut.isPending}
                  onClick={() =>
                    setConfirm({
                      title: "Run the stale-scan watchdog?",
                      description:
                        "Re-queues expired leases and fails any scan past the stale threshold — the same pass the scheduler runs every 60s.",
                      confirmLabel: "Run watchdog",
                      onConfirm: () => watchdogMut.mutate(),
                    })
                  }
                >
                  Run watchdog now
                </Button>
              }
            />
          </div>
          <SectionState
            loading={stuckQ.isLoading}
            error={stuckQ.isError ? stuckQ.error : undefined}
            empty={(stuckQ.data ?? []).length === 0}
            emptyTitle="No stuck scans"
            emptyMessage="Every scan is in a terminal state."
          >
            <table className="w-full text-sm">
              <thead>
                <tr className="text-meta text-fg-muted uppercase border-y border-line">
                  <th className="text-left px-4 py-2 font-semibold">Source</th>
                  <th className="text-left px-4 py-2 font-semibold">Status</th>
                  <th className="text-left px-4 py-2 font-semibold">Age</th>
                  <th className="text-left px-4 py-2 font-semibold">Last heartbeat</th>
                  <th className="text-left px-4 py-2 font-semibold">Scanner</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {(stuckQ.data ?? []).map((s) => (
                  <tr
                    key={s.scan_id}
                    className="border-b border-line-subtle last:border-b-0 hover:bg-surface-muted"
                  >
                    <td className="px-4 py-2 text-fg">
                      {s.source_name ?? <span className="text-fg-subtle">— deleted —</span>}
                      <span className="text-fg-subtle"> · {s.scan_type}</span>
                    </td>
                    <td className="px-4 py-2">
                      <Badge variant={scanBadge(s.status)}>{s.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-fg-muted tabular-nums">
                      {dur(s.age_seconds)}
                    </td>
                    <td className="px-4 py-2 text-fg-muted">
                      {rel(s.last_heartbeat_at)}
                    </td>
                    <td className="px-4 py-2 text-fg-muted">
                      {s.assigned_scanner_name ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <Button
                        size="sm"
                        variant="danger"
                        loading={cancelMut.isPending && cancelMut.variables === s.scan_id}
                        onClick={() =>
                          setConfirm({
                            title: "Cancel this scan?",
                            description: `The scan on ${s.source_name ?? "a deleted source"} will be marked cancelled and its source freed.`,
                            confirmLabel: "Cancel scan",
                            destructive: true,
                            onConfirm: () => cancelMut.mutate(s.scan_id),
                          })
                        }
                      >
                        Cancel
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </SectionState>
        </Card>

        {/* — Scan logs — */}
        <Card>
          <CardHeader
            title="Scan logs"
            description={`Live-log lines are kept for ${ov?.log_retention_days ?? 7} days after a scan finishes, then swept automatically. Purge sooner here.`}
          />
          <div className="flex flex-wrap items-end gap-3">
            <Input
              label="Delete logs of terminal scans older than"
              type="number"
              min={0}
              value={purgeDays}
              onChange={(e) => setPurgeDays(e.target.value)}
              hint="days · 0 clears every finished scan's logs"
              containerClassName="w-72"
            />
            <Button
              variant="danger"
              loading={purgeMut.isPending}
              onClick={() => {
                const days = Math.max(0, parseInt(purgeDays, 10) || 0);
                setConfirm({
                  title: "Purge scan logs?",
                  description: `Delete log lines for completed, failed and cancelled scans older than ${days} day${days === 1 ? "" : "s"}. This cannot be undone.`,
                  confirmLabel: "Purge logs",
                  destructive: true,
                  onConfirm: () => purgeMut.mutate(days),
                });
              }}
            >
              Purge logs
            </Button>
            <div className="text-sm text-fg-muted">
              {(ov?.scan_log_purgeable ?? 0).toLocaleString()} rows past the{" "}
              {ov?.log_retention_days ?? 7}-day window right now.
            </div>
          </div>
        </Card>

        {/* — Index & backfills — */}
        <Card padding="none">
          <div className="px-4 pt-4">
            <CardHeader
              title="Index & backfills"
              description="Long-running jobs run in the background — start one and watch its progress below."
            />
          </div>
          <div className="px-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
            {JOB_KINDS.map((j) => {
              const running = runningKinds.has(j.kind);
              return (
                <div
                  key={j.kind}
                  className="border border-line rounded-lg p-3 flex items-start justify-between gap-3"
                >
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-fg">{j.label}</div>
                    <div className="text-xs text-fg-muted mt-0.5">{j.description}</div>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={running}
                    loading={jobMut.isPending && jobMut.variables === j.kind}
                    reserveLabel="Running…"
                    onClick={() =>
                      setConfirm({
                        title: `Start: ${j.label}?`,
                        description: j.description,
                        confirmLabel: "Start job",
                        onConfirm: () => jobMut.mutate(j.kind),
                      })
                    }
                  >
                    {running ? "Running…" : "Run"}
                  </Button>
                </div>
              );
            })}
          </div>
          <SectionState
            loading={jobsQ.isLoading}
            error={jobsQ.isError ? jobsQ.error : undefined}
            empty={jobs.length === 0}
            emptyTitle="No jobs yet"
            emptyMessage="Recently run maintenance jobs appear here."
          >
            <table className="w-full text-sm mt-3">
              <thead>
                <tr className="text-meta text-fg-muted uppercase border-y border-line">
                  <th className="text-left px-4 py-2 font-semibold">Job</th>
                  <th className="text-left px-4 py-2 font-semibold">Status</th>
                  <th className="text-left px-4 py-2 font-semibold">Rows</th>
                  <th className="text-left px-4 py-2 font-semibold">Started</th>
                  <th className="text-left px-4 py-2 font-semibold">Result</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr
                    key={j.id}
                    className="border-b border-line-subtle last:border-b-0 hover:bg-surface-muted"
                  >
                    <td className="px-4 py-2 text-fg">{jobLabel(j.kind)}</td>
                    <td className="px-4 py-2">
                      <Badge variant={jobBadge(j.status)}>{j.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-fg-muted tabular-nums">
                      {j.result?.rows_affected != null
                        ? j.result.rows_affected.toLocaleString()
                        : "—"}
                    </td>
                    <td className="px-4 py-2 text-fg-muted">{rel(j.started_at)}</td>
                    <td className="px-4 py-2 text-fg-muted truncate max-w-xs" title={j.error ?? ""}>
                      {j.error ?? (j.status === "succeeded" ? "OK" : "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </SectionState>
        </Card>

        {/* — Scanner diagnostics — */}
        <Card padding="none">
          <div className="px-4 pt-4">
            <CardHeader
              title="Scanner diagnostics"
              description="Build version and liveness of every registered scanner. A version behind the newest is the usual cause of proxy-size scan failures."
              action={
                <Link
                  to="/settings/scanners"
                  className="text-sm text-accent-600 hover:underline"
                >
                  Manage scanners →
                </Link>
              }
            />
          </div>
          <SectionState
            loading={scannersQ.isLoading}
            error={scannersQ.isError ? scannersQ.error : undefined}
            empty={(scannersQ.data ?? []).length === 0}
            emptyTitle="No scanners registered"
            emptyMessage="Register a scanner from Settings → Scanners."
          >
            <table className="w-full text-sm">
              <thead>
                <tr className="text-meta text-fg-muted uppercase border-y border-line">
                  <th className="text-left px-4 py-2 font-semibold">Scanner</th>
                  <th className="text-left px-4 py-2 font-semibold">Host</th>
                  <th className="text-left px-4 py-2 font-semibold">Version</th>
                  <th className="text-left px-4 py-2 font-semibold">Last seen</th>
                  <th className="text-left px-4 py-2 font-semibold">State</th>
                </tr>
              </thead>
              <tbody>
                {(scannersQ.data ?? []).map((s) => {
                  const behind =
                    !!s.version && !!newest && s.version !== newest;
                  return (
                    <tr
                      key={s.id}
                      className="border-b border-line-subtle last:border-b-0 hover:bg-surface-muted"
                    >
                      <td className="px-4 py-2 text-fg">{s.name}</td>
                      <td className="px-4 py-2 text-fg-muted">{s.hostname ?? "—"}</td>
                      <td className="px-4 py-2">
                        {s.version ? (
                          <span className="inline-flex items-center gap-1.5">
                            <span className="font-mono text-xs text-fg">{s.version}</span>
                            {behind && (
                              <Badge variant="failed" title={`Newest reporting is ${newest}`}>
                                outdated
                              </Badge>
                            )}
                          </span>
                        ) : (
                          <span className="text-fg-subtle">unknown</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-fg-muted">{rel(s.last_seen_at)}</td>
                      <td className="px-4 py-2">
                        <Badge variant={s.online ? "online" : "offline"}>
                          {s.online ? "online" : "offline"}
                        </Badge>
                        {!s.enabled && (
                          <Badge variant="neutral" className="ml-1.5">
                            disabled
                          </Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </SectionState>
        </Card>
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ""}
        description={confirm?.description}
        confirmLabel={confirm?.confirmLabel ?? "Confirm"}
        destructive={confirm?.destructive}
        onConfirm={() => {
          confirm?.onConfirm();
          setConfirm(null);
        }}
        onCancel={() => setConfirm(null)}
      />
    </Page>
  );
}

function jobLabel(kind: string): string {
  return JOB_KINDS.find((j) => j.kind === kind)?.label ?? kind;
}
