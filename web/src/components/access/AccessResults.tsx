import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  StatCard,
  Table,
} from "../ui";
import type { Column } from "../ui";
import { formatBytes, formatNumber } from "../../lib/format";

interface PrincipalToFiles {
  principal: { token: string; name: string | null; domain: string | null; kind: string };
  right: string;
  summary: { file_count: number; total_size_bytes: number; source_count: number };
  by_source: { source_id: string; source_name: string; file_count: number }[];
  sample: SampleHit[];
  next_offset: number | null;
}

interface SampleHit {
  id: string;
  source_id: string;
  path: string;
  filename: string;
  size_bytes: number | null;
  owner_name?: string | null;
  fs_modified_at?: number | null;
}

interface FileToPrincipals {
  entry_id: string;
  path: string;
  filename: string;
  right: string;
  principals: {
    token: string;
    name?: string | null;
    domain?: string | null;
    kind: string;
    source: string;
  }[];
}

const KIND_BADGE_VARIANT: Record<string, "online" | "info" | "neutral" | "failed"> = {
  user: "online",
  group: "info",
  wildcard: "failed",
  unknown: "neutral",
};

export function PrincipalToFilesResults({
  data,
  onSelectEntry,
}: {
  data: PrincipalToFiles;
  onSelectEntry: (id: string) => void;
}) {
  const cols: Column<SampleHit>[] = [
    {
      key: "filename",
      header: "Name",
      // The Table component doesn't expose onRowClick, so the filename
      // becomes a button. That keeps the click target obvious and
      // accessible (real <button>, focusable, screen-reader-friendly)
      // rather than a row-wide ghost click handler.
      render: (f) => (
        <button
          type="button"
          onClick={() => onSelectEntry(f.id)}
          className="font-medium text-fg hover:text-accent-700 text-left truncate"
        >
          {f.filename}
        </button>
      ),
    },
    {
      key: "size",
      header: "Size",
      render: (f) => (
        <span className="tabular-nums text-fg">
          {f.size_bytes != null ? formatBytes(f.size_bytes) : "—"}
        </span>
      ),
    },
    {
      key: "owner",
      header: "Owner",
      render: (f) => <span className="text-fg-muted">{f.owner_name ?? "—"}</span>,
    },
    {
      key: "path",
      header: "Path",
      render: (f) => (
        <span className="text-xs text-fg-subtle font-mono break-all">{f.path}</span>
      ),
    },
  ];

  // The principal header — friendly name when we have one, otherwise the
  // raw token. Domain only renders when distinct from the SID itself.
  const headlineName = data.principal.name
    ? `${data.principal.domain ? data.principal.domain + "\\" : ""}${data.principal.name}`
    : data.principal.token;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-fg">{headlineName}</h2>
        <Badge variant={KIND_BADGE_VARIANT[data.principal.kind] ?? "neutral"}>
          {data.principal.kind}
        </Badge>
        {data.principal.name && (
          <code className="font-mono text-xs text-fg-subtle bg-surface-muted px-1.5 py-0.5 rounded">
            {data.principal.token}
          </code>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <StatCard
          label={`Files this principal can ${data.right}`}
          value={formatNumber(data.summary.file_count)}
        />
        <StatCard label="Total size" value={formatBytes(data.summary.total_size_bytes)} />
        <StatCard
          label="Sources"
          value={`${data.summary.source_count}`}
        />
      </div>

      {data.by_source.length > 0 && (
        <Card padding="md">
          <CardHeader title="By source" description="File counts per indexed source." />
          <ul className="space-y-2">
            {data.by_source.map((s) => {
              // Bar widths are proportional to the source with the most files.
              // No log scaling; the spread is usually small enough that linear
              // reads correctly and saves the cognitive cost of a non-linear
              // axis on what's a quick glance card.
              const max = Math.max(...data.by_source.map((r) => r.file_count));
              const pct = max > 0 ? (s.file_count / max) * 100 : 0;
              return (
                <li key={s.source_id} className="flex items-center gap-3 text-sm">
                  <span className="w-40 truncate text-fg">{s.source_name}</span>
                  <div className="flex-1 h-2 bg-surface-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent-500"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="tabular-nums text-fg-muted w-20 text-right">
                    {formatNumber(s.file_count)}
                  </span>
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      <Card padding="md">
        <CardHeader
          title="Sample"
          description={
            data.next_offset != null
              ? `${data.sample.length} of many — next page available`
              : `${data.sample.length} files`
          }
        />
        <Table<SampleHit>
          columns={cols}
          data={data.sample}
          rowKey={(f) => f.id}
          emptyTitle="No matches"
          emptyDescription={`This principal has no ${data.right} access to anything indexed.`}
        />
      </Card>
    </div>
  );
}

export function FileToPrincipalsResults({
  data,
  onSelectEntry,
}: {
  data: FileToPrincipals;
  onSelectEntry: () => void;
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-baseline gap-3">
        <button
          type="button"
          onClick={onSelectEntry}
          className="text-lg font-semibold text-fg hover:text-accent-700"
        >
          {data.filename}
        </button>
        <code className="font-mono text-xs text-fg-subtle break-all">{data.path}</code>
      </div>

      <Card padding="md">
        <CardHeader
          title={`Principals with ${data.right}`}
          description={`${data.principals.length} grants`}
        />
        {data.principals.length === 0 ? (
          <EmptyState
            title="No grants"
            description={`Nothing in this entry's ACL grants ${data.right} access. The file is effectively unreachable for that right.`}
          />
        ) : (
          <ul className="space-y-2">
            {data.principals.map((p) => (
              <li key={p.token} className="flex items-center gap-3 text-sm">
                <Badge variant={KIND_BADGE_VARIANT[p.kind] ?? "neutral"}>{p.kind}</Badge>
                <span className="font-medium text-fg">
                  {p.name
                    ? `${p.domain ? p.domain + "\\" : ""}${p.name}`
                    : tokenToFriendly(p.token)}
                </span>
                {p.name && (
                  <code className="font-mono text-xs text-fg-subtle">{p.token}</code>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** Pretty-print the canonical token vocabulary for unresolved cases.
 * `posix:gid:1001` → "POSIX gid 1001"; `*` → "Anyone"; etc. */
function tokenToFriendly(token: string): string {
  if (token === "*") return "Anyone";
  if (token === "auth") return "Authenticated users";
  if (token.startsWith("sid:")) return token.slice(4);
  if (token.startsWith("posix:uid:")) return `POSIX uid ${token.slice("posix:uid:".length)}`;
  if (token.startsWith("posix:gid:")) return `POSIX gid ${token.slice("posix:gid:".length)}`;
  if (token.startsWith("nfsv4:GROUP:")) return `NFSv4 group ${token.slice("nfsv4:GROUP:".length)}`;
  if (token.startsWith("nfsv4:")) return `NFSv4 ${token.slice("nfsv4:".length)}`;
  if (token.startsWith("s3:user:")) return `S3 user ${token.slice("s3:user:".length)}`;
  return token;
}
