import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Badge, Button, ConfirmDialog, Drawer } from "../ui";
import {
  useDeleteHost,
  useHostDetail,
  useTestHostConnection,
  useUpdateHost,
} from "../../hooks/useHosts";
import { useSources } from "../../hooks/useSources";
import { useAuth } from "../../hooks/useAuth";
import {
  HostFields,
  type HostConfig,
  type HostType,
  validateHostConfig,
} from "../sources/source-fields/HostFields";
import { DiscoverSharesPanel } from "./DiscoverSharesPanel";

// Host types whose protocol exposes a "shares" enumeration. Local
// has no host; SSH has no shares. The Discover button is hidden for
// either.
const DISCOVERABLE: ReadonlySet<string> = new Set(["smb", "nfs", "s3"]);

interface Props {
  hostId: string | null;
  open: boolean;
  onClose: () => void;
  /** When true and the host supports discovery, auto-expand the
   *  Discover Shares panel on open. Used by the deep-link from
   *  AddSourceForm's "Or discover all shares on this host" link. */
  autoDiscover?: boolean;
}

export function HostDetail({ hostId, open, onClose, autoDiscover }: Props) {
  const { isAdmin } = useAuth();
  const hostQuery = useHostDetail(hostId);
  const sourcesQuery = useSources();
  const updateHost = useUpdateHost();
  const deleteHost = useDeleteHost();
  const testHost = useTestHostConnection();

  const host = hostQuery.data;
  const attachedSources = useMemo(
    () => (sourcesQuery.data ?? []).filter((s) => s.host_id === hostId),
    [sourcesQuery.data, hostId],
  );

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftConfig, setDraftConfig] = useState<HostConfig>({});
  const [error, setError] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    if (host) {
      setDraftName(host.name);
      setDraftConfig((host.connection_config ?? {}) as HostConfig);
      setEditing(false);
      setError(null);
      // Honour the deep-link's autoDiscover only when the host type
      // actually supports discovery (smb/nfs/s3). Otherwise keep the
      // panel collapsed.
      setDiscovering(Boolean(autoDiscover) && DISCOVERABLE.has(host.type));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host?.id, autoDiscover]);

  if (!hostId) return null;

  const validationError = host
    ? validateHostConfig(host.type as HostType, draftConfig)
    : null;

  async function handleSave() {
    setError(null);
    if (!host) return;
    if (validationError) {
      setError(validationError);
      return;
    }
    // Strip "***" sentinels so the wire payload is clean — the
    // backend's secret-merge would preserve them anyway.
    const cleaned: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(draftConfig)) {
      if (v === "***") continue;
      cleaned[k] = v;
    }
    const p = updateHost.mutateAsync({
      id: host.id,
      data: { name: draftName, connection_config: cleaned },
    });
    toast.promise(p, {
      loading: "Saving…",
      success: "Host updated.",
      error: (e: unknown) =>
        `Save failed: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      await p;
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function handleTest() {
    if (!host) return;
    const p = testHost.mutateAsync(host.id);
    toast.promise(p, {
      loading: "Testing…",
      success: (r) =>
        r.result.ok
          ? "Reachable."
          : `Unreachable: ${r.result.step ?? "error"}: ${r.result.error ?? "unknown"}`,
      error: (e: unknown) =>
        `Test failed: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      await p;
    } catch {
      // toast surfaced
    }
  }

  function handleDelete() {
    if (!host) return;
    if (attachedSources.length > 0) {
      toast.error(
        `Host has ${attachedSources.length} attached source(s); detach or delete them first.`,
      );
      return;
    }
    setConfirmingDelete(true);
  }

  async function performDelete() {
    if (!host) return;
    const p = deleteHost.mutateAsync(host.id);
    toast.promise(p, {
      loading: "Deleting…",
      success: `Deleted "${host.name}".`,
      error: (e: unknown) =>
        `Delete failed: ${e instanceof Error ? e.message : "unknown error"}`,
    });
    try {
      await p;
      setConfirmingDelete(false);
      onClose();
    } catch (e) {
      setConfirmingDelete(false);
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="md"
      title={
        host ? (
          <div className="flex items-center gap-2">
            <span>{host.name}</span>
            <Badge variant="neutral">{host.type}</Badge>
          </div>
        ) : (
          "Host"
        )
      }
    >
      <div className="px-6 py-5 space-y-4">
        {hostQuery.isLoading && <p className="text-sm text-fg-muted">Loading…</p>}
        {host && (
          <>
            {!editing ? (
              <DisplayRows
                host={host}
                attachedCount={attachedSources.length}
              />
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-fg mb-1">
                    Name
                  </label>
                  <input
                    type="text"
                    value={draftName}
                    onChange={(e) => setDraftName(e.target.value)}
                    className="w-full rounded-md border border-line px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                  />
                </div>
                <HostFields
                  type={host.type as HostType}
                  value={draftConfig}
                  onChange={setDraftConfig}
                />
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
                    onClick={handleTest}
                    loading={testHost.isPending}
                  >
                    Test connection
                  </Button>
                  {isAdmin && DISCOVERABLE.has(host.type) && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setDiscovering((d) => !d)}
                      title="Enumerate shares using this host's credentials and add the ones you want as Source rows."
                    >
                      {discovering ? "Hide shares" : "Discover shares"}
                    </Button>
                  )}
                  {isAdmin && (
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={handleDelete}
                      loading={deleteHost.isPending}
                      disabled={attachedSources.length > 0}
                      title={
                        attachedSources.length > 0
                          ? "Detach or delete attached sources first"
                          : undefined
                      }
                    >
                      Delete
                    </Button>
                  )}
                </>
              ) : (
                <>
                  <Button
                    size="sm"
                    onClick={handleSave}
                    loading={updateHost.isPending}
                    disabled={!!validationError}
                  >
                    Save
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditing(false);
                      setDraftName(host.name);
                      setDraftConfig((host.connection_config ?? {}) as HostConfig);
                      setError(null);
                    }}
                  >
                    Cancel
                  </Button>
                </>
              )}
            </div>

            {discovering && DISCOVERABLE.has(host.type) && (
              <div className="pt-3 border-t border-line-subtle">
                <p className="text-xs uppercase tracking-wide text-fg-subtle mb-2">
                  Discover shares
                </p>
                <DiscoverSharesPanel
                  host={host}
                  onAdded={() => setDiscovering(false)}
                />
              </div>
            )}

            {attachedSources.length > 0 && (
              <div className="pt-3 border-t border-line-subtle">
                <p className="text-xs uppercase tracking-wide text-fg-subtle mb-2">
                  Attached shares ({attachedSources.length})
                </p>
                <ul className="space-y-1 text-sm">
                  {attachedSources.map((s) => (
                    <li
                      key={s.id}
                      className="flex items-center justify-between gap-2 text-fg"
                    >
                      <span className="font-medium">{s.name}</span>
                      <span className="text-xs text-fg-muted font-mono truncate">
                        {s.summary ?? ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
      <ConfirmDialog
        open={confirmingDelete}
        title={host ? `Delete host "${host.name}"?` : "Delete host?"}
        description="This removes the host and its credentials. It cannot be undone."
        confirmLabel="Delete"
        destructive
        loading={deleteHost.isPending}
        onConfirm={performDelete}
        onCancel={() => !deleteHost.isPending && setConfirmingDelete(false)}
      />
    </Drawer>
  );
}

function DisplayRows({
  host,
  attachedCount,
}: {
  host: { type: string; connection_config: Record<string, unknown> };
  attachedCount: number;
}) {
  const fieldRows = useMemo(
    () =>
      Object.entries(host.connection_config ?? {}).map(([k, v]) => ({
        key: k,
        isMasked: v === "***",
        display: typeof v === "string" ? v : JSON.stringify(v),
      })),
    [host.connection_config],
  );

  return (
    <dl className="text-sm space-y-2">
      <Row label="Type">
        <span>{host.type}</span>
      </Row>
      <Row label="Shares">
        <span className="text-fg-muted">{attachedCount}</span>
      </Row>
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
