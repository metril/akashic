/**
 * Box source-config form (v0.18.0).
 *
 * Tier 4 PR 3. OAuth-shaped via the existing ``box`` provider in the
 * OAuth registry. Same Sign-in popup pattern as Drive / OneDrive /
 * SharePoint / Dropbox; folder_id scope is optional (Box uses opaque
 * folder ids — the literal "0" is the All Files root).
 *
 * JWT app-auth (server-to-server, RSA-signed) is on the roadmap as
 * a second variant — not in this release.
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
import type { BoxConfig, FieldsProps } from "../sourceTypes";

export function BoxFields({ value, onChange }: FieldsProps<BoxConfig>) {
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
        provider: "box",
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
          "Box OAuth client isn't configured. Go to Settings → OAuth providers.",
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
            Connect a Box account to grant Akashic read-only access.
          </p>
          <Button onClick={handleSignIn} disabled={busy || start.isPending}>
            {busy || start.isPending ? "Opening…" : "Sign in with Box"}
          </Button>
          <p className="text-[11px] text-fg-muted mt-2">
            Requires a Box OAuth client configured under Settings → OAuth
            providers (with <code>root_readonly</code> scope).
          </p>
        </div>
      )}

      <Input
        label="Folder ID (optional)"
        value={value.folder_id ?? ""}
        onChange={(e) => onChange({ ...value, folder_id: e.target.value })}
        placeholder="(leave empty to scan all files; Box's root is folder id 0)"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Numeric Box folder id (the trailing segment of a Box folder URL).
        Empty walks the user's All Files root.
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
