/**
 * Google Drive source-config form (v0.14.0).
 *
 * Tier 1 PR-C. OAuth-shaped — there's no static credential to paste;
 * the user clicks "Sign in with Google", goes through the consent
 * flow in a popup, and the resulting OAuth grant is stored on the API
 * keyed by source_id (set later, when the source row is created).
 *
 * The form holds the ``oauth_credential_id`` returned from
 * ``/api/oauth/start`` in mode=associate. Until that's set, save is
 * blocked by ``validateSourceConfig``.
 *
 * Folder scope: optional. When empty, the scanner walks My Drive
 * root. Power users with massive drives paste a folder ID here to
 * limit indexing to one project subtree.
 *
 * Pre-requisite: an OAuth provider config for Google must exist (see
 * Settings → OAuth providers). When it doesn't, the popup fails with
 * a 412 — the form surfaces a link to fix that.
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
import type { FieldsProps, GDriveConfig } from "../sourceTypes";

export function GDriveFields({ value, onChange }: FieldsProps<GDriveConfig>) {
  const start = useStartOAuth();
  const credentials = useOAuthCredentials();
  const [busy, setBusy] = useState(false);

  // The credential row that matches our currently-stored
  // oauth_credential_id, when present. Used to render
  // "Connected as alice@example.com" without an extra fetch.
  const connected = (credentials.data ?? []).find(
    (c) => c.id === value.oauth_credential_id,
  );

  // If oauth_credential_id is set but the row is gone (admin disconnected
  // it from Settings), clear our local state so the user re-signs in
  // before saving.
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
        provider: "google",
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
      // Surface the precondition-failed (412) case explicitly so the
      // user knows where to go.
      if (msg.includes("not configured")) {
        toast.error(
          "Google OAuth client isn't configured. Go to Settings → OAuth providers.",
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
      // ignore — we still want to clear the local state
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
            Connect a Google account to grant Akashic read-only Drive access.
          </p>
          <Button onClick={handleSignIn} disabled={busy || start.isPending}>
            {busy || start.isPending ? "Opening…" : "Sign in with Google"}
          </Button>
          <p className="text-[11px] text-fg-muted mt-2">
            Requires a Google OAuth client configured under Settings →
            OAuth providers.
          </p>
        </div>
      )}

      <Input
        label="Folder ID (optional)"
        value={value.folder_id ?? ""}
        onChange={(e) => onChange({ ...value, folder_id: e.target.value })}
        placeholder="(leave empty to scan all of My Drive)"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Paste a Drive folder ID (the part after <code>/folders/</code> in the
        Drive URL) to scope the scan. Useful for large drives where you only
        want to index one project tree.
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
