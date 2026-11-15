import { useState } from "react";
import { toast } from "sonner";
import { useScopes } from "../hooks/useScopes";
import { useAllTokens, useMyTokens, useTokenMutations } from "../hooks/useApiTokens";
import { useSources } from "../hooks/useSources";
import { formatDateTime, formatRelative } from "../lib/format";
import type { AdminApiToken, ApiToken } from "../api/types";
import { Badge, Button, Card, CardHeader, ConfirmDialog, Input, SectionState } from "../components/ui";
import { CreateTokenDialog } from "../components/tokens/CreateTokenDialog";

type View = "mine" | "all";

function status(t: ApiToken): { label: string; variant: "online" | "failed" | "offline" } {
  if (t.revoked_at) return { label: "Revoked", variant: "failed" };
  if (t.expires_at && new Date(t.expires_at) <= new Date()) return { label: "Expired", variant: "offline" };
  return { label: "Active", variant: "online" };
}

interface RowProps {
  token: ApiToken | AdminApiToken;
  sourceNames: Map<string, string>;
  showOwner: boolean;
  renaming: boolean;
  onRename?: (t: ApiToken) => void;
  onRevoke: (t: ApiToken) => void;
}

function TokenRow({ token, sourceNames, showOwner, renaming, onRename, onRevoke }: RowProps) {
  const st = status(token);
  return (
    <tr className="border-t border-line-subtle align-top">
      <td className="py-2 pr-3">
        <div className="text-sm font-medium text-fg">{token.name}</div>
        <code className="font-mono text-xs text-fg-muted">{token.token_prefix}…</code>
        {showOwner && "username" in token && <div className="text-xs text-fg-muted">{token.username}</div>}
      </td>
      <td className="py-2 pr-3 max-w-xs">
        <div className="flex flex-wrap gap-1">
          {token.scopes.map((s) => (
            <span key={s} className="font-mono text-[11px] rounded bg-surface-muted px-1.5 py-0.5 text-fg-muted">
              {s}
            </span>
          ))}
        </div>
      </td>
      <td className="py-2 pr-3 text-xs text-fg-muted">
        {token.source_ids === null
          ? "All sources"
          : token.source_ids.map((id) => sourceNames.get(id) ?? "Unknown source").join(", ")}
      </td>
      <td className="py-2 pr-3 text-xs text-fg-muted">{token.expires_at ? formatDateTime(token.expires_at) : "Never"}</td>
      <td className="py-2 pr-3 text-xs text-fg-muted">{token.last_used_at ? formatRelative(token.last_used_at) : "Never"}</td>
      <td className="py-2 pr-3">
        <Badge variant={st.variant}>{st.label}</Badge>
      </td>
      <td className="py-2 text-right whitespace-nowrap">
        {onRename && !token.revoked_at && (
          <Button size="sm" variant="ghost" onClick={() => onRename(token)} disabled={renaming}>
            Rename
          </Button>
        )}
        {!token.revoked_at && (
          <Button size="sm" variant="danger" onClick={() => onRevoke(token)}>
            Revoke
          </Button>
        )}
      </td>
    </tr>
  );
}

function TokenTable(props: Omit<RowProps, "token"> & { tokens: ApiToken[] | undefined; loading: boolean; error: unknown }) {
  const { tokens, loading, error, ...row } = props;
  return (
    <SectionState
      loading={loading}
      error={error}
      empty={(tokens ?? []).length === 0}
      emptyTitle="No tokens"
      emptyMessage="Tokens you create show up here."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="text-xs uppercase tracking-wide text-fg-subtle">
              <th className="pb-2 pr-3 font-medium">Name</th>
              <th className="pb-2 pr-3 font-medium">Scopes</th>
              <th className="pb-2 pr-3 font-medium">Sources</th>
              <th className="pb-2 pr-3 font-medium">Expires</th>
              <th className="pb-2 pr-3 font-medium">Last used</th>
              <th className="pb-2 pr-3 font-medium">Status</th>
              <th className="pb-2" />
            </tr>
          </thead>
          <tbody>
            {(tokens ?? []).map((t) => (
              <TokenRow key={t.id} token={t} {...row} />
            ))}
          </tbody>
        </table>
      </div>
    </SectionState>
  );
}

export default function ApiTokens() {
  const { has } = useScopes();
  const canAdmin = has("tokens:admin");
  const canWrite = has("tokens:write");
  const [view, setView] = useState<View>("mine");
  const [createOpen, setCreateOpen] = useState(false);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const [renameTarget, setRenameTarget] = useState<ApiToken | null>(null);
  const [newName, setNewName] = useState("");
  const mine = useMyTokens();
  const all = useAllTokens(canAdmin && view === "all");
  const sources = useSources();
  const { rename, revoke, adminRevoke } = useTokenMutations();
  const sourceNames = new Map((sources.data ?? []).map((s) => [s.id, s.name]));
  const revokeMut = view === "all" ? adminRevoke : revoke;

  function confirmRevoke() {
    if (!revoking) return;
    revokeMut.mutate(revoking.id, {
      onSuccess: () => {
        toast.success(`Revoked "${revoking.name}"`);
        setRevoking(null);
      },
      onError: (err) => toast.error(err.message),
    });
  }

  function submitRename() {
    if (!renameTarget || !newName.trim()) return;
    rename.mutate(
      { id: renameTarget.id, name: newName.trim() },
      {
        onSuccess: () => setRenameTarget(null),
        onError: (err) => toast.error(err.message),
      },
    );
  }

  return (
    <div className="space-y-4">
      {canAdmin && (
        <div role="tablist" aria-label="Token view" className="inline-flex rounded-md border border-line p-0.5">
          {(["mine", "all"] as const).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={`px-3 py-1 text-sm rounded ${view === v ? "bg-surface-muted text-fg font-medium" : "text-fg-muted"}`}
            >
              {v === "mine" ? "My tokens" : "All tokens"}
            </button>
          ))}
        </div>
      )}

      <Card padding="md">
        <CardHeader
          title={view === "all" ? "All tokens" : "API tokens"}
          description={
            view === "all"
              ? "Every user's tokens. You can revoke any of them."
              : "Personal tokens for scripts and integrations. A token can never do more than you can."
          }
          action={
            view === "mine" && canWrite ? <Button onClick={() => setCreateOpen(true)}>+ New token</Button> : undefined
          }
        />
        {view === "all" ? (
          <TokenTable
            tokens={all.data}
            loading={all.isLoading}
            error={all.error}
            sourceNames={sourceNames}
            showOwner
            renaming={false}
            onRevoke={setRevoking}
          />
        ) : (
          <TokenTable
            tokens={mine.data}
            loading={mine.isLoading}
            error={mine.error}
            sourceNames={sourceNames}
            showOwner={false}
            renaming={rename.isPending}
            onRename={canWrite ? (t) => { setRenameTarget(t); setNewName(t.name); } : undefined}
            onRevoke={setRevoking}
          />
        )}
      </Card>

      {canWrite && createOpen && <CreateTokenDialog onClose={() => setCreateOpen(false)} />}

      <ConfirmDialog
        open={revoking !== null}
        title="Revoke token?"
        description={
          <>
            "{revoking?.name}" stops working immediately. This can't be undone.
          </>
        }
        confirmLabel="Revoke"
        destructive
        loading={revokeMut.isPending}
        onConfirm={confirmRevoke}
        onCancel={() => setRevoking(null)}
      />

      <ConfirmDialog
        open={renameTarget !== null}
        title="Rename token"
        description={
          <Input
            label="Name"
            value={newName}
            maxLength={100}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitRename()}
          />
        }
        confirmLabel="Save"
        loading={rename.isPending}
        onConfirm={submitRename}
        onCancel={() => setRenameTarget(null)}
      />
    </div>
  );
}
