/**
 * Admin-only "System Status" page (v0.29.0).
 *
 * Answers "is anything actually happening?" — surfaces the live
 * activity counters from Tika (RQ extraction queue depth, throughput,
 * last-extraction) and Meilisearch (document count, pending tasks,
 * last task status) that previously were only visible by grepping
 * worker logs.
 *
 * Liveness chips at top, two activity cards below. 10 s poll while
 * the page is mounted; the underlying API endpoint is 5 s cached so
 * back-to-back polls share a snapshot.
 */
import { Badge, Card, CardHeader, EmptyState, Page, Spinner, StatCard } from "../components/ui";
import {
  useServicesActivity,
  useServicesHealth,
  type ServiceLiveness,
} from "../hooks/useServicesHealth";

function formatRelative(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const delta = Date.now() - then;
  if (delta < 0) return "just now";
  if (delta < 5_000) return "just now";
  if (delta < 60_000) return `${Math.floor(delta / 1000)} s ago`;
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} min ago`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} h ago`;
  return `${Math.floor(delta / 86_400_000)} d ago`;
}

function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat().format(n);
}

function lastTaskVariant(status: string | null): "online" | "failed" | "neutral" {
  if (status === "succeeded") return "online";
  if (status === "failed") return "failed";
  return "neutral";
}

function LivenessChip({ name, probe }: { name: string; probe: ServiceLiveness }) {
  return (
    <div className="flex items-center justify-between gap-2 px-3 py-2 rounded-md border border-line bg-surface">
      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            probe.ok ? "bg-emerald-500" : "bg-rose-500"
          }`}
        />
        <span className="text-sm font-medium">{name}</span>
      </div>
      <div className="text-xs text-fg-muted">
        {probe.ok ? (
          probe.latency_ms !== null ? `${probe.latency_ms} ms` : "ok"
        ) : (
          <span className="text-rose-500" title={probe.error ?? "down"}>down</span>
        )}
      </div>
    </div>
  );
}

export default function AdminSystemStatus() {
  const health = useServicesHealth();
  const activity = useServicesActivity();

  return (
    <Page
      title="System Status"
      description="Backend service liveness + activity surface."
      width="wide"
    >
      {/* Liveness row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {health.data ? (
          <>
            <LivenessChip name="Postgres" probe={health.data.postgres} />
            <LivenessChip name="Redis" probe={health.data.redis} />
            <LivenessChip name="Meilisearch" probe={health.data.meilisearch} />
            <LivenessChip name="Tika" probe={health.data.tika} />
          </>
        ) : health.isError ? (
          <div className="col-span-full">
            <EmptyState
              title="Failed to load service health"
              description={String(health.error)}
            />
          </div>
        ) : (
          <div className="col-span-full flex items-center gap-2 text-fg-muted text-sm">
            <Spinner /> Loading service liveness…
          </div>
        )}
      </div>

      {/* Activity — Tika */}
      <Card padding="md" className="mb-5">
        <CardHeader
          title="Tika — text extraction"
          description="Per-document text extraction; jobs land here after every ingest batch."
        />
        {activity.isError ? (
          <EmptyState title="Activity unavailable" description={String(activity.error)} />
        ) : !activity.data ? (
          <div className="flex items-center gap-2 text-fg-muted text-sm py-4"><Spinner /> Loading activity…</div>
        ) : !activity.data.tika.ok ? (
          <EmptyState
            title="Counters unreachable"
            description={activity.data.tika.error ?? "Redis connection failed"}
          />
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
            <StatCard
              label="Queue depth"
              value={formatNumber(activity.data.tika.queue_depth)}
              subtext="jobs waiting"
            />
            <StatCard
              label="Last 5 min"
              value={formatNumber(activity.data.tika.extracted_last_5min)}
              subtext="documents extracted"
            />
            <StatCard
              label="Failed"
              value={formatNumber(activity.data.tika.failed_count)}
              subtext={
                (activity.data.tika.failed_count ?? 0) > 0 ? (
                  <span className="flex items-center gap-1">
                    <span>last {formatRelative(activity.data.tika.last_failed_at)}</span>
                    <a
                      className="text-accent hover:underline"
                      href="/api/health/services/extraction/failed"
                      target="_blank"
                      rel="noreferrer"
                    >
                      view
                    </a>
                  </span>
                ) : (
                  "no failures"
                )
              }
            />
            <StatCard
              label="Last extraction"
              value={formatRelative(activity.data.tika.last_extracted_at)}
              subtext={`${formatNumber(activity.data.tika.extracted_total)} total`}
            />
          </div>
        )}
      </Card>

      {/* Activity — Meilisearch */}
      <Card padding="md">
        <CardHeader
          title="Meilisearch — search index"
          description="Document count + pending task surface from the search engine itself."
        />
        {activity.isError ? (
          <EmptyState title="Activity unavailable" description={String(activity.error)} />
        ) : !activity.data ? (
          <div className="flex items-center gap-2 text-fg-muted text-sm py-4"><Spinner /> Loading activity…</div>
        ) : !activity.data.meilisearch.ok ? (
          <EmptyState
            title="Meilisearch unreachable"
            description={activity.data.meilisearch.error ?? "GET failed"}
          />
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
            <StatCard
              label="Documents"
              value={formatNumber(activity.data.meilisearch.documents_in_index)}
              subtext="in `files` index"
            />
            <StatCard
              label="Pending tasks"
              value={formatNumber(activity.data.meilisearch.pending_tasks)}
              subtext={
                (activity.data.meilisearch.pending_tasks ?? 0) > 0
                  ? "indexer running"
                  : "idle"
              }
            />
            <StatCard
              label="Last task"
              value={formatRelative(activity.data.meilisearch.last_task_at)}
              subtext={activity.data.meilisearch.last_task_status ?? undefined}
            />
            <div className="flex items-center justify-center">
              {activity.data.meilisearch.last_task_status && (
                <Badge variant={lastTaskVariant(activity.data.meilisearch.last_task_status)}>
                  {activity.data.meilisearch.last_task_status}
                </Badge>
              )}
            </div>
          </div>
        )}
      </Card>
    </Page>
  );
}
