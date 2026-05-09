import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  ModalShell,
  Page,
  Spinner,
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
    <Page
      title="OAuth providers"
      description="Connect Akashic to providers that use OAuth (Google Drive, OneDrive, Dropbox). Each deployment registers its own OAuth app with the provider and pastes the client credentials here."
      width="default"
    >
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
          <Button
            size="sm"
            onClick={() => setAdding(true)}
            disabled={providers.isLoading}
          >
            + Add provider
          </Button>
        </div>

        {providers.isLoading ? (
          <div className="flex items-center justify-center py-8 text-fg-subtle">
            <Spinner />
          </div>
        ) : providers.isError ? (
          <p className="text-sm text-rose-600">
            {providers.error instanceof Error
              ? providers.error.message
              : "Failed to load providers"}
          </p>
        ) : configured.length === 0 ? (
          <p className="text-xs text-fg-muted py-4 text-center">
            No OAuth providers configured yet. Click{" "}
            <span className="font-medium">+ Add provider</span> to register
            one.
          </p>
        ) : (
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
        )}
      </Card>

      <Card padding="md">
        <h3 className="text-sm font-semibold text-fg mb-1">Connected accounts</h3>
        <p className="text-xs text-fg-muted mb-3">
          OAuth grants the API has stored. Use{" "}
          <span className="font-medium">Refresh</span> to verify token
          rotation works end-to-end against the provider.
        </p>

        {credentials.isLoading ? (
          <div className="flex items-center justify-center py-8 text-fg-subtle">
            <Spinner />
          </div>
        ) : credentials.isError ? (
          <p className="text-sm text-rose-600">
            {credentials.error instanceof Error
              ? credentials.error.message
              : "Failed to load credentials"}
          </p>
        ) : (credentials.data ?? []).length === 0 ? (
          <p className="text-xs text-fg-muted py-4 text-center">
            No connected accounts yet. Add a provider above, then use{" "}
            <span className="font-medium">Test</span> on its row to verify.
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {(credentials.data ?? []).map((c) => (
              <CredentialRow
                key={c.id}
                cred={c}
                onDelete={() => setConfirmDeleteCred(c)}
              />
            ))}
          </ul>
        )}
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
    </Page>
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
    <div className="flex items-center justify-between rounded-md border border-line px-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-fg">
            {PROVIDER_LABELS[summary.provider]}
          </span>
          <Badge variant="online">Configured</Badge>
        </div>
        <p className="text-[11px] text-fg-muted mt-0.5 truncate">
          client_id: <code>{summary.client_id}</code> · redirect:{" "}
          <code>{summary.redirect_uri}</code>
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Button size="sm" variant="ghost" onClick={onEdit}>
          Edit
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={handleTest}
          disabled={start.isPending}
        >
          {start.isPending ? "Opening…" : "Test"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDelete}>
          Forget
        </Button>
      </div>
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
          disabled={refresh.isPending}
        >
          {refresh.isPending ? "Refreshing…" : "Refresh"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDelete}>
          Disconnect
        </Button>
      </div>
    </li>
  );
}
