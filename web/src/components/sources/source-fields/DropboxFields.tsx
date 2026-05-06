/**
 * Dropbox source-config form (v0.17.0+).
 *
 * Tier 4 PR 2. OAuth-shaped via the existing ``dropbox`` provider in
 * the OAuth registry. Same Sign-in popup pattern as the Drive /
 * OneDrive / SharePoint forms; the only Dropbox-specific bit is the
 * optional path-scope input (Dropbox uses paths as canonical
 * identifiers rather than opaque ids).
 *
 * v0.18.1 added best-effort cloud_drive ACL enrichment — the
 * scanner calls ``list_folder_members`` / ``list_file_members``
 * for items whose ``sharing_info`` flags them as explicitly shared
 * and maps Dropbox's access_type onto cloud_drive roles.
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
import type { DropboxConfig, FieldsProps } from "../sourceTypes";

export function DropboxFields({ value, onChange }: FieldsProps<DropboxConfig>) {
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
        provider: "dropbox",
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
          "Dropbox OAuth client isn't configured. Go to Settings → OAuth providers.",
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
      // ignore — clearing local state is what matters
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
            Connect a Dropbox account to grant Akashic read-only access.
          </p>
          <Button onClick={handleSignIn} disabled={busy || start.isPending}>
            {busy || start.isPending ? "Opening…" : "Sign in with Dropbox"}
          </Button>
          <p className="text-[11px] text-fg-muted mt-2">
            Requires a Dropbox OAuth client configured under Settings →
            OAuth providers (with <code>files.metadata.read</code> +{" "}
            <code>files.content.read</code> + <code>sharing.read</code>).
          </p>
        </div>
      )}

      <Input
        label="Path (optional)"
        value={value.path ?? ""}
        onChange={(e) => onChange({ ...value, path: e.target.value })}
        placeholder="(leave empty to scan all of Dropbox)"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Folder path within Dropbox to scope the scan, e.g. <code>/Reports</code>.
        Empty walks from the user's Dropbox root.
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
