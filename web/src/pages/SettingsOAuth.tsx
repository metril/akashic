import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Icon,
  ModalShell,
  SectionState,
} from "../components/ui";
import { AddProviderWizard } from "../components/oauth/AddProviderWizard";
import { ProviderForm } from "../components/oauth/ProviderForm";
import {
  PROVIDER_LABELS,
  useDeleteOAuthCredential,
  useDeleteOAuthProvider,
  useOAuthCredentials,
  useOAuthProviders,
  useRefreshOAuthCredential,
  useStartOAuth,
  type OAuthCredentialSummary,
  type OAuthProviderName,
  type OAuthProviderSummary,
} from "../hooks/useOAuthProviders";
import { openOAuthPopup } from "../lib/oauthPopup";

/**
 * OAuth foundation settings.
 *
 * Two stacked sections:
 *
 *  1. **Provider apps** — only providers with a stored client_id /
 *     client_secret render here. The "+ Add OAuth provider" button
 *     opens a wizard that picks from the not-yet-configured providers
 *     and reuses `ProviderForm` (the same form the edit-existing path
 *     uses). Pre-v0.25 the page rendered all three providers as
 *     always-visible rows; the wizard collapses that into one entry
 *     point and gives a clean slot for future provider types.
 *
 *  2. **Connected accounts** — the OAuth grants the API has stored.
 *     "Test" / "Refresh" buttons verify the round-trip against the
 *     live provider before any scanner connector consumes them.
 */
export default function SettingsOAuth() {
  const providers = useOAuthProviders();
  const credentials = useOAuthCredentials();

  const [editing, setEditing] = useState<OAuthProviderName | null>(null);
  const [adding, setAdding] = useState(false);
  const [confirmDeleteProvider, setConfirmDeleteProvider] =
    useState<OAuthProviderSummary | null>(null);
  const [confirmDeleteCred, setConfirmDeleteCred] =
    useState<OAuthCredentialSummary | null>(null);

  const deleteProvider = useDeleteOAuthProvider();
  const deleteCred = useDeleteOAuthCredential();

  const configured = useMemo(
    () =>
      (providers.data ?? []).filter(
        (p) => p.has_secret && p.client_id !== "",
      ),
    [providers.data],
  );

  return (
    <section aria-labelledby="settings-oauth-heading">
      <header className="mb-5">
        <h2 id="settings-oauth-heading" className="sr-only">
          OAuth providers
        </h2>
        <p className="text-sm text-fg-muted">
          Connect Akashic to providers that use OAuth (Google Drive, OneDrive,
          Dropbox). Each deployment registers its own OAuth app with the
          provider and pastes the client credentials here.
        </p>
      </header>

      <Card padding="md" className="mb-6">
        <div className="flex items-start justify-between mb-3 gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-fg mb-1">Provider apps</h3>
            <p className="text-xs text-fg-muted">
              Register an OAuth app with each provider's developer console
              and paste the client credentials. Set the redirect URI to{" "}
              <code className="text-[11px]">
                {window.location.origin}/api/oauth/callback
              </code>
              .
            </p>
          </div>
          {configured.length > 0 && (
            <Button
              size="sm"
              onClick={() => setAdding(true)}
              disabled={providers.isLoading}
            >
              + Add provider
            </Button>
          )}
        </div>

        <SectionState
          loading={providers.isLoading}
          error={providers.isError ? providers.error : undefined}
          empty={configured.length === 0}
          emptyTitle="No OAuth providers configured"
          emptyMessage="Add Google, Microsoft, or Dropbox to let Akashic ingest from cloud drives."
          emptyAction={
            <Button onClick={() => setAdding(true)}>+ Add provider</Button>
          }
        >
          <div className="space-y-2">
            {configured.map((p) => (
              <ProviderRow
                key={p.provider}
                summary={p}
                onEdit={() => setEditing(p.provider)}
                onDelete={() => setConfirmDeleteProvider(p)}
              />
            ))}
          </div>
        </SectionState>
      </Card>

      <Card padding="md">
        <h3 className="text-sm font-semibold text-fg mb-1">Connected accounts</h3>
        <p className="text-xs text-fg-muted mb-3">
          OAuth grants the API has stored. Use{" "}
          <span className="font-medium">Refresh</span> to verify token
          rotation works end-to-end against the provider.
        </p>

        <SectionState
          loading={credentials.isLoading}
          error={credentials.isError ? credentials.error : undefined}
          empty={(credentials.data ?? []).length === 0}
          emptyTitle="No connected accounts yet"
          emptyMessage="Add a provider above, then use Test on its row to verify the round-trip and connect an account."
        >
          <ul className="divide-y divide-line">
            {(credentials.data ?? []).map((c) => (
              <CredentialRow
                key={c.id}
                cred={c}
                onDelete={() => setConfirmDeleteCred(c)}
              />
            ))}
          </ul>
        </SectionState>
      </Card>

      {adding && (
        <AddProviderWizard
          providers={providers.data ?? []}
          onClose={() => setAdding(false)}
        />
      )}

      {editing !== null && (
        <ProviderEditor
          provider={editing}
          existing={configured.find((p) => p.provider === editing) ?? null}
          onClose={() => setEditing(null)}
        />
      )}

      <ConfirmDialog
        open={confirmDeleteProvider !== null}
        title="Forget provider config?"
        description={
          confirmDeleteProvider
            ? `This drops the client_id/secret for ${PROVIDER_LABELS[confirmDeleteProvider.provider]}. Existing connected accounts keep working until their refresh tokens expire.`
            : ""
        }
        confirmLabel="Forget"
        destructive
        onConfirm={async () => {
          if (!confirmDeleteProvider) return;
          try {
            await deleteProvider.mutateAsync(confirmDeleteProvider.provider);
            toast.success(`Removed ${PROVIDER_LABELS[confirmDeleteProvider.provider]}.`);
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't remove provider");
          }
          setConfirmDeleteProvider(null);
        }}
        onCancel={() => setConfirmDeleteProvider(null)}
      />

      <ConfirmDialog
        open={confirmDeleteCred !== null}
        title="Disconnect account?"
        description={
          confirmDeleteCred
            ? confirmDeleteCred.source_name
              ? `Disconnect ${confirmDeleteCred.account_email ?? "this account"} from ${PROVIDER_LABELS[confirmDeleteCred.provider]}. Source "${confirmDeleteCred.source_name}" depends on this credential — it will fail to scan until you reauthorize.`
              : `Disconnect ${confirmDeleteCred.account_email ?? "this account"} from ${PROVIDER_LABELS[confirmDeleteCred.provider]}. Any source attached to this credential will fail to scan until it's reauthorized.`
            : ""
        }
        confirmLabel="Disconnect"
        destructive
        onConfirm={async () => {
          if (!confirmDeleteCred) return;
          try {
            await deleteCred.mutateAsync(confirmDeleteCred.id);
            toast.success("Disconnected.");
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't disconnect");
          }
          setConfirmDeleteCred(null);
        }}
        onCancel={() => setConfirmDeleteCred(null)}
      />
    </section>
  );
}

function ProviderRow({
  summary,
  onEdit,
  onDelete,
}: {
  summary: OAuthProviderSummary;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const start = useStartOAuth();

  async function handleTest() {
    try {
      const { authorization_url } = await start.mutateAsync({
        provider: summary.provider,
        mode: "test",
      });
      const result = await openOAuthPopup(authorization_url);
      if (result.ok) {
        toast.success(
          `${PROVIDER_LABELS[summary.provider]}: connected as ${result.account_email}.`,
        );
      } else {
        toast.error(`Test failed: ${result.error}`);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed");
    }
  }

  return (
    <div className="rounded-md border border-line px-3 py-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium text-fg">
            {PROVIDER_LABELS[summary.provider]}
          </span>
          <Badge variant="online">Configured</Badge>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button size="sm" variant="ghost" onClick={onEdit}>
            Edit
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleTest}
            loading={start.isPending}
          >
            Test
          </Button>
          <Button size="sm" variant="ghost" onClick={onDelete}>
            Forget
          </Button>
        </div>
      </div>
      <CopyableField label="client_id" value={summary.client_id} />
      <CopyableField label="redirect" value={summary.redirect_uri} />
    </div>
  );
}

function CopyableField({ label, value }: { label: string; value: string }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`Copied ${label}`);
    } catch {
      toast.error("Couldn't copy to clipboard");
    }
  }
  return (
    <div className="flex items-center gap-2 text-xs text-fg-muted">
      <span className="w-20 shrink-0">{label}:</span>
      <code className="flex-1 truncate font-mono text-fg" title={value}>
        {value}
      </code>
      <button
        type="button"
        onClick={copy}
        title={`Copy ${label}`}
        className="shrink-0 rounded p-1 text-fg-subtle hover:bg-surface-muted hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
      >
        <Icon
          path="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2m0 0h2a2 2 0 0 1 2 2v3"
          className="h-3.5 w-3.5"
        />
      </button>
    </div>
  );
}

function ProviderEditor({
  provider,
  existing,
  onClose,
}: {
  provider: OAuthProviderName;
  existing: OAuthProviderSummary | null;
  onClose: () => void;
}) {
  return (
    <ModalShell
      open
      onClose={onClose}
      maxWidth="md"
      ariaLabelledBy="oauth-provider-title"
    >
      <div className="p-5 space-y-3">
        <div>
          <p className="text-xs text-fg-muted">Settings → OAuth providers</p>
          <h2 id="oauth-provider-title" className="text-base font-semibold text-fg">
            {PROVIDER_LABELS[provider]} OAuth app
          </h2>
        </div>
        <p className="text-xs text-fg-muted">
          Paste the credentials from your provider's developer console.
          The secret is encrypted at rest with a key derived from{" "}
          <code>AKASHIC_SECRET_KEY</code>.
        </p>
        <ProviderForm
          provider={provider}
          existing={existing}
          onSaved={onClose}
          onCancel={onClose}
        />
      </div>
    </ModalShell>
  );
}

function CredentialRow({
  cred,
  onDelete,
}: {
  cred: OAuthCredentialSummary;
  onDelete: () => void;
}) {
  const refresh = useRefreshOAuthCredential();

  async function handleRefresh() {
    try {
      const r = await refresh.mutateAsync(cred.id);
      const expires = r.expires_at
        ? new Date(r.expires_at).toLocaleString()
        : "no expiry returned";
      toast.success(`Refreshed. Access token expires: ${expires}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Refresh failed");
    }
  }

  const expiresLabel = cred.access_token_expires_at
    ? new Date(cred.access_token_expires_at).toLocaleString()
    : "—";

  return (
    <li className="flex items-center justify-between py-2.5">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-fg">
            {PROVIDER_LABELS[cred.provider]}
          </span>
          <Badge variant="neutral">
            {cred.account_email ?? cred.account_label ?? "?"}
          </Badge>
          {cred.source_id === null && <Badge variant="info">Unattached</Badge>}
          {cred.source_name && (
            <Badge variant="neutral" title="Source this credential is in use by">
              Used by: {cred.source_name}
            </Badge>
          )}
        </div>
        <p className="text-[11px] text-fg-muted mt-0.5">
          Connected {new Date(cred.created_at).toLocaleDateString()} · access expires{" "}
          {expiresLabel}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Button
          size="sm"
          variant="ghost"
          onClick={handleRefresh}
          loading={refresh.isPending}
        >
          Refresh
        </Button>
        <Button size="sm" variant="ghost" onClick={onDelete}>
          Disconnect
        </Button>
      </div>
    </li>
  );
}
