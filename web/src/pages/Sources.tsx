import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  useSources,
  useDeleteSource,
} from "../hooks/useSources";
import { api } from "../api/client";
import {
  Card,
  Button,
  Badge,
  Skeleton,
  EmptyState,
} from "../components/ui";
import type { BadgeVariant } from "../components/ui";
import type { Source } from "../types";
import { formatDate } from "../lib/format";
import { BucketSecurityCard } from "../components/acl/BucketSecurityCard";
import { AddSourceForm } from "../components/sources/AddSourceForm";

const KNOWN_STATUSES: BadgeVariant[] = [
  "online",
  "offline",
  "scanning",
  "failed",
];

function statusVariant(status: string): BadgeVariant {
  return (KNOWN_STATUSES as string[]).includes(status)
    ? (status as BadgeVariant)
    : "neutral";
}

function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function SourceCard({ source }: { source: Source }) {
  const deleteSource = useDeleteSource();
  const queryClient = useQueryClient();

  const triggerScan = useMutation({
    mutationFn: (sourceId: string) =>
      api.post("/scans/trigger", {
        source_id: sourceId,
        scan_type: "incremental",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  const path =
    typeof source.connection_config?.path === "string"
      ? source.connection_config.path
      : JSON.stringify(source.connection_config);

  function handleDelete() {
    if (confirm(`Delete source "${source.name}"?`)) {
      deleteSource.mutate(source.id);
    }
  }

  const canScan = source.status !== "scanning";

  return (
    <Card padding="md" className="flex flex-col">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-base font-semibold text-gray-900 truncate">
          {source.name}
        </h3>
        <Badge variant={statusVariant(source.status)}>
          {statusLabel(source.status)}
        </Badge>
      </div>
      <p className="text-xs text-gray-500 font-mono break-all mb-3">{path}</p>
      <dl className="text-xs text-gray-500 space-y-1 mb-4">
        <div className="flex gap-2">
          <dt className="text-gray-400">Type</dt>
          <dd>{source.type}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-gray-400">Last scan</dt>
          <dd>{formatDate(source.last_scan_at)}</dd>
        </div>
      </dl>
      <div className="mt-auto flex items-center gap-2 pt-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={() => triggerScan.mutate(source.id)}
          disabled={!canScan}
          loading={triggerScan.isPending}
        >
          {source.status === "scanning" ? "Scanning…" : "Scan now"}
        </Button>
        <Button
          size="sm"
          variant="danger"
          onClick={handleDelete}
          loading={deleteSource.isPending}
        >
          Delete
        </Button>
      </div>
      {triggerScan.isError && (
        <p className="text-xs text-rose-600 mt-2">
          {triggerScan.error instanceof Error
            ? triggerScan.error.message
            : "Failed to trigger scan"}
        </p>
      )}
      {source.type === "s3" && <BucketSecurityCard source={source} />}
    </Card>
  );
}

export default function Sources() {
  const { data: sources, isLoading, error } = useSources();

  return (
    <div className="px-8 py-7 max-w-7xl">
      <div className="mb-7 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
            Sources
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Filesystem locations Akashic indexes and watches.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-4">
          {isLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Skeleton className="h-44" />
              <Skeleton className="h-44" />
            </div>
          ) : error ? (
            <Card>
              <p className="text-sm text-rose-600">
                {error instanceof Error
                  ? error.message
                  : "Error loading sources"}
              </p>
            </Card>
          ) : (sources ?? []).length === 0 ? (
            <Card padding="lg">
              <EmptyState
                title="No sources yet"
                description="Add your first source on the right to start indexing."
              />
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {(sources ?? []).map((s) => (
                <SourceCard key={s.id} source={s} />
              ))}
            </div>
          )}
        </div>

        <div>
          <AddSourceForm />
        </div>
      </div>
    </div>
  );
}
