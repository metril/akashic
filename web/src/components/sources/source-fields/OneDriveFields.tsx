/**
 * OneDrive source-config form (v0.15.0).
 *
 * Tier 1 PR-C part 2. OAuth-shaped via Microsoft Graph; the same
 * pattern as GDriveFields. The user signs in with Microsoft, the
 * popup callback persists a SourceOAuthCredential row with
 * source_id=NULL, and this form carries the credential id forward
 * until the source is created.
 *
 * Pre-requisite: the deployment owner has configured a Microsoft
 * OAuth app under Settings → OAuth providers (Azure App
 * Registration with Files.Read + Files.Read.All + offline_access
 * scopes).
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button, Input } from "../../ui";
import { api } from "../../../api/client";
import {
  useStartOAuth,
  useOAuthCredentials,
  type OAuthCredentialSummary,
} from "../../../hooks/useOAuthProviders";
import { openOAuthPopup } from "../../../lib/oauthPopup";
import type { FieldsProps, OneDriveConfig } from "../sourceTypes";

export function OneDriveFields({
  value,
  onChange,
}: FieldsProps<OneDriveConfig>) {
  const start = useStartOAuth();
  const credentials = useOAuthCredentials();
  const [busy, setBusy] = useState(false);

  const connected = (credentials.data ?? []).find(
    (c) => c.id === value.oauth_credential_id,
  );

  useEffect(() => {
    if (
      value.oauth_credential_id &&
      credentials.data &&
      !credentials.data.find((c) => c.id === value.oauth_credential_id)
    ) {
      onChange({ ...value, oauth_credential_id: undefined });
    }
  }, [credentials.data, value, onChange]);

  async function handleSignIn() {
    setBusy(true);
    try {
      const { authorization_url } = await start.mutateAsync({
        provider: "microsoft",
        mode: "associate",
      });
      const result = await openOAuthPopup(authorization_url);
      if (!result.ok) {
        toast.error(`Sign-in failed: ${result.error}`);
        return;
      }
      if (!result.credential_id) {
        toast.error("Sign-in succeeded but no credential id was returned");
        return;
      }
      onChange({ ...value, oauth_credential_id: result.credential_id });
      toast.success(`Connected as ${result.account_email}.`);
      credentials.refetch();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "unknown error";
      if (msg.includes("not configured")) {
        toast.error(
          "Microsoft OAuth client isn't configured. Go to Settings → OAuth providers.",
        );
      } else {
        toast.error(`Sign-in failed: ${msg}`);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleDisconnect() {
    if (!value.oauth_credential_id) return;
    try {
      await api.delete(`/oauth/credentials/${value.oauth_credential_id}`);
    } catch {
      // ignore — clearing the local state is what matters
    }
    onChange({ ...value, oauth_credential_id: undefined });
    credentials.refetch();
  }

  return (
    <div className="space-y-3">
      {connected ? (
        <ConnectedAccount
          credential={connected}
          onDisconnect={handleDisconnect}
        />
      ) : (
        <div className="rounded-md border border-dashed border-line p-4 text-center">
          <p className="text-sm text-fg mb-2">
            Connect a Microsoft account to grant Akashic read-only OneDrive
            access.
          </p>
          <Button onClick={handleSignIn} loading={busy || start.isPending}>
            Sign in with Microsoft
          </Button>
          <p className="text-[11px] text-fg-muted mt-2">
            Requires a Microsoft OAuth client configured under Settings →
            OAuth providers (Azure App Registration with Files.Read +
            offline_access).
          </p>
        </div>
      )}

      <Input
        label="Item ID (optional)"
        value={value.item_id ?? ""}
        onChange={(e) => onChange({ ...value, item_id: e.target.value })}
        placeholder="(leave empty to scan all of OneDrive)"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Paste a OneDrive folder's item ID to scope the scan. The id is
        the alphanumeric token in URLs of the form
        <code> /drive/items/&lt;item-id&gt;</code>.
      </p>
    </div>
  );
}

function ConnectedAccount({
  credential,
  onDisconnect,
}: {
  credential: OAuthCredentialSummary;
  onDisconnect: () => void;
}) {
  return (
    <div className="rounded-md border border-line p-3 flex items-center justify-between">
      <div className="min-w-0">
        <p className="text-sm text-fg font-medium">
          Connected as{" "}
          <span className="font-mono">
            {credential.account_email ?? credential.account_label ?? "?"}
          </span>
        </p>
        <p className="text-[11px] text-fg-muted mt-0.5">
          Akashic stores a refresh token for this account; access tokens are
          minted on demand at scan time.
        </p>
      </div>
      <Button size="sm" variant="ghost" onClick={onDisconnect}>
        Disconnect
      </Button>
    </div>
  );
}
