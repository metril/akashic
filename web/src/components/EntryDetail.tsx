import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EntryDetail as EntryDetailT } from "../types";
import { formatBytes, formatDateTime } from "../lib/format";
import { formatMode, formatOctal } from "../lib/perms";
import { Badge, Spinner, EmptyState } from "./ui";
import { ACLSection } from "./acl/ACLSection";
import { ACLDiff } from "./acl/ACLDiff";
import { S3ExposureBanner } from "./acl/S3ExposureBanner";
import { EffectivePermissions } from "./acl/EffectivePermissions";
import { ContentTab } from "./entry-detail/ContentTab";

interface Props {
  entryId: string | null;
}

function Section({
  title,
  children,
  empty,
}: {
  title: string;
  children: React.ReactNode;
  empty?: boolean;
}) {
  return (
    <section className="px-6 py-4 border-b border-gray-100 last:border-b-0">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-3">
        {title}
      </h3>
      {empty ? (
        <p className="text-sm text-gray-400 italic">None</p>
      ) : (
        children
      )}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 text-sm py-1">
      <dt className="w-32 flex-shrink-0 text-xs text-gray-500">{label}</dt>
      <dd className="min-w-0 flex-1 text-gray-800 break-words">{children}</dd>
    </div>
  );
}

const Mono = ({ children }: { children: React.ReactNode }) => (
  <code className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded text-gray-700">
    {children}
  </code>
);

export function EntryDetail({ entryId }: Props) {
  const query = useQuery<EntryDetailT>({
    queryKey: ["entry", entryId],
    queryFn: () => api.get<EntryDetailT>(`/entries/${entryId}`),
    enabled: !!entryId,
  });

  if (!entryId) return null;

  if (query.isLoading) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400">
        <Spinner />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="p-6">
        <EmptyState
          title="Couldn't load entry"
          description={
            query.error instanceof Error ? query.error.message : "Unknown error"
          }
        />
      </div>
    );
  }
  const entry = query.data;
  if (!entry) return null;

  return (
    <div className="divide-y divide-gray-100">
      <S3ExposureBanner source={entry.source as import("../types").Source | undefined} />
      <Section title="Identity">
        <dl>
          <Row label="Path">
            <Mono>{entry.path}</Mono>
          </Row>
          <Row label="Kind">
            <Badge variant={entry.kind === "directory" ? "info" : "neutral"}>
              {entry.kind}
            </Badge>
          </Row>
          {entry.extension && (
            <Row label="Extension">
              <Mono>.{entry.extension}</Mono>
            </Row>
          )}
          {entry.mime_type && <Row label="MIME">{entry.mime_type}</Row>}
        </dl>
      </Section>

      {entry.kind === "file" && (
        <Section title="Content">
          <ContentTab entry={entry} />
        </Section>
      )}

      <Section title="Permissions">
        <dl>
          <Row label="Mode">
            <span className="font-mono text-sm text-gray-900">
              {formatMode(entry.mode)}
            </span>{" "}
            <span className="text-xs text-gray-400 ml-2">
              ({formatOctal(entry.mode)})
            </span>
          </Row>
          <Row label="Owner">
            {entry.owner_name || "—"}
            <span className="text-xs text-gray-400 ml-1.5">
              (uid {entry.uid ?? "?"})
            </span>
          </Row>
          <Row label="Group">
            {entry.group_name || "—"}
            <span className="text-xs text-gray-400 ml-1.5">
              (gid {entry.gid ?? "?"})
            </span>
          </Row>
        </dl>
      </Section>

      <ACLSection acl={entry.acl} sourceId={entry.source_id} />

      <EffectivePermissions key={entry.id} entryId={entry.id} acl={entry.acl} />

      <Section
        title="Extended attributes"
        empty={!entry.xattrs || Object.keys(entry.xattrs).length === 0}
      >
        {entry.xattrs && Object.keys(entry.xattrs).length > 0 && (
          <dl className="space-y-2">
            {Object.entries(entry.xattrs).map(([k, v]) => (
              <div key={k} className="text-sm">
                <Mono>{k}</Mono>{" "}
                <span className="text-gray-700 break-all">{v}</span>
              </div>
            ))}
          </dl>
        )}
      </Section>

      {entry.kind === "file" && (
        <Section title="Content">
          <dl>
            {entry.size_bytes != null && (
              <Row label="Size">
                {formatBytes(entry.size_bytes)}{" "}
                <span className="text-xs text-gray-400 ml-1.5">
                  ({entry.size_bytes.toLocaleString()} bytes)
                </span>
              </Row>
            )}
            {entry.content_hash && (
              <Row label="Hash">
                <Mono>{entry.content_hash}</Mono>
              </Row>
            )}
          </dl>
        </Section>
      )}

      <Section title="Timestamps">
        <dl>
          <Row label="Modified">{formatDateTime(entry.fs_modified_at)}</Row>
          <Row label="Accessed">{formatDateTime(entry.fs_accessed_at)}</Row>
          <Row label="Created">{formatDateTime(entry.fs_created_at)}</Row>
          <Row label="First seen">{formatDateTime(entry.first_seen_at)}</Row>
          <Row label="Last seen">{formatDateTime(entry.last_seen_at)}</Row>
        </dl>
      </Section>

      <Section
        title="Version history"
        empty={entry.versions.length === 0}
      >
        {entry.versions.length > 0 && (
          <ol className="space-y-3">
            {entry.versions.map((v, i) => {
              const prev = entry.versions[i + 1];
              const nonAclChanges: string[] = [];
              let aclChanged = false;
              if (prev) {
                if (v.content_hash !== prev.content_hash) nonAclChanges.push("content");
                if (v.size_bytes !== prev.size_bytes) nonAclChanges.push("size");
                if (v.mode !== prev.mode) nonAclChanges.push("mode");
                if (v.uid !== prev.uid || v.gid !== prev.gid) nonAclChanges.push("ownership");
                if (
                  JSON.stringify(v.xattrs ?? null) !==
                  JSON.stringify(prev.xattrs ?? null)
                )
                  nonAclChanges.push("xattrs");
                if (
                  JSON.stringify(v.acl ?? null) !==
                  JSON.stringify(prev.acl ?? null)
                )
                  aclChanged = true;
              }
              const label = !prev
                ? "First observation"
                : nonAclChanges.length === 0 && !aclChanged
                  ? "Re-observed (no field changed)"
                  : nonAclChanges.length > 0
                    ? `Changed: ${nonAclChanges.join(", ")}`
                    : null;
              return (
                <li
                  key={v.id}
                  className="border-l-2 border-accent-200 pl-3 py-0.5"
                >
                  <div className="text-xs text-gray-500">
                    {formatDateTime(v.detected_at)}
                  </div>
                  {label && (
                    <div className="text-sm text-gray-800 mt-0.5">{label}</div>
                  )}
                  {aclChanged && prev && (
                    <ACLDiff prev={prev.acl} curr={v.acl} />
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </Section>
    </div>
  );
}
