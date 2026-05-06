/**
 * Box source-config form (v0.18.0 + v0.19.0).
 *
 * Tier 4 PR 3. Two auth modes:
 *
 *  - **OAuth** (default, v0.18.0) — same Sign-in popup pattern as
 *    Drive / OneDrive / SharePoint / Dropbox. Refresh-token grant
 *    persisted in SourceOAuthCredential.
 *  - **JWT app-auth** (v0.19.0) — server-to-server. Operator pastes
 *    Box client_id + secret, enterprise_id, public_key_id (kid), and
 *    the RSA private key from a Box Custom App registration. The API
 *    signs a short-lived RS256 JWT at each lease and exchanges it
 *    against ``/oauth2/token`` for an access token.
 *
 * Either way the scanner side is identical — both paths inject the
 * minted access token into ``connection_config["access_token"]`` and
 * the connector consumes the same shape.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button, Input, MaskedInput } from "../../ui";
import { api } from "../../../api/client";
import {
  useStartOAuth,
  useOAuthCredentials,
  type OAuthCredentialSummary,
} from "../../../hooks/useOAuthProviders";
import { openOAuthPopup } from "../../../lib/oauthPopup";
import type { BoxAuthMode, BoxConfig, FieldsProps } from "../sourceTypes";

const MODE_LABELS: Record<BoxAuthMode, string> = {
  oauth: "OAuth (sign in)",
  jwt: "JWT app-auth (server-to-server)",
};

export function BoxFields({ value, onChange }: FieldsProps<BoxConfig>) {
  const mode: BoxAuthMode = value.auth_mode ?? "oauth";

  return (
    <div className="space-y-3">
      <ModeToggle
        mode={mode}
        onChange={(next) => onChange({ ...value, auth_mode: next })}
      />

      {mode === "oauth" ? (
        <OAuthSection value={value} onChange={onChange} />
      ) : (
        <JWTSection value={value} onChange={onChange} />
      )}

      <Input
        label="Folder ID (optional)"
        value={value.folder_id ?? ""}
        onChange={(e) => onChange({ ...value, folder_id: e.target.value })}
        placeholder="(leave empty to scan all files; Box's root is folder id 0)"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Numeric Box folder id (the trailing segment of a Box folder URL).
        Empty walks the All Files root visible to the connected account.
      </p>
    </div>
  );
}

function ModeToggle({
  mode,
  onChange,
}: {
  mode: BoxAuthMode;
  onChange: (next: BoxAuthMode) => void;
}) {
  return (
    <label className="flex flex-col text-xs text-fg-muted">
      Authentication
      <select
        className="mt-1 text-sm border border-line rounded px-2 py-1 bg-surface text-fg"
        value={mode}
        onChange={(e) => onChange(e.target.value as BoxAuthMode)}
      >
        {(Object.keys(MODE_LABELS) as BoxAuthMode[]).map((m) => (
          <option key={m} value={m}>
            {MODE_LABELS[m]}
          </option>
        ))}
      </select>
    </label>
  );
}

function OAuthSection({ value, onChange }: FieldsProps<BoxConfig>) {
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

  if (connected) {
    return (
      <ConnectedAccount
        credential={connected}
        onDisconnect={handleDisconnect}
      />
    );
  }
  return (
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
  );
}

function JWTSection({ value, onChange }: FieldsProps<BoxConfig>) {
  // The masked-sentinel values come back from the API as "***".
  // Show a placeholder explaining the unchanged-on-blank behavior.
  const isMasked = (v: string | undefined) => v === "***";
  return (
    <div className="space-y-3 rounded-md border border-line p-3">
      <p className="text-xs text-fg-muted">
        Server-to-server auth — no user popup. Set up a Custom App in
        the Box developer console with "Server Authentication (with
        JWT)" enabled, get it authorized in your enterprise admin
        console, then paste the credentials below.
      </p>
      <Input
        label="Client ID"
        value={value.client_id ?? ""}
        onChange={(e) => onChange({ ...value, client_id: e.target.value })}
        required
      />
      <MaskedInput
        label={
          isMasked(value.client_secret)
            ? "Client secret (replace)"
            : "Client secret"
        }
        value={isMasked(value.client_secret) ? "" : value.client_secret ?? ""}
        onChange={(e) =>
          onChange({ ...value, client_secret: e.target.value })
        }
        placeholder={
          isMasked(value.client_secret)
            ? "(unchanged — type to replace)"
            : ""
        }
        required={!isMasked(value.client_secret)}
        autoComplete="new-password"
      />
      <Input
        label="Enterprise ID"
        value={value.enterprise_id ?? ""}
        onChange={(e) =>
          onChange({ ...value, enterprise_id: e.target.value })
        }
        placeholder="123456"
        required
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Numeric tenant id from the Box admin console — also visible in
        the JSON the developer console exports under{" "}
        <code>enterpriseID</code>.
      </p>
      <Input
        label="Public Key ID (kid)"
        value={value.public_key_id ?? ""}
        onChange={(e) =>
          onChange({ ...value, public_key_id: e.target.value })
        }
        placeholder="abcd1234"
        required
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Short id Box assigned to your public key when you uploaded
        it. Lives at <code>boxAppSettings.appAuth.publicKeyID</code> in
        the exported JSON.
      </p>
      <label className="flex flex-col text-xs text-fg-muted">
        {isMasked(value.private_key)
          ? "Private key (replace)"
          : "Private key (RSA PEM)"}
        <textarea
          className="mt-1 text-xs font-mono border border-line rounded px-2 py-1 bg-surface text-fg min-h-[8rem]"
          value={isMasked(value.private_key) ? "" : value.private_key ?? ""}
          onChange={(e) =>
            onChange({ ...value, private_key: e.target.value })
          }
          placeholder={
            isMasked(value.private_key)
              ? "(unchanged — paste a new key to replace)"
              : "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
          }
        />
      </label>
      <MaskedInput
        label={
          isMasked(value.private_key_passphrase)
            ? "Private key passphrase (replace, only if encrypted)"
            : "Private key passphrase (optional, only if encrypted)"
        }
        value={
          isMasked(value.private_key_passphrase)
            ? ""
            : value.private_key_passphrase ?? ""
        }
        onChange={(e) =>
          onChange({ ...value, private_key_passphrase: e.target.value })
        }
        placeholder={
          isMasked(value.private_key_passphrase)
            ? "(unchanged — type to replace)"
            : ""
        }
        autoComplete="new-password"
      />
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
