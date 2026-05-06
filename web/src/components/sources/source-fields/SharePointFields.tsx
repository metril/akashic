/**
 * SharePoint document-library source-config form (v0.16.0).
 *
 * Tier 1 PR-C part 3. Same Microsoft OAuth + Graph machinery as
 * OneDriveFields; the only structural addition is the site_id input
 * and an optional drive_id (sites with multiple document libraries).
 *
 * Site ID format: Graph addresses sites with a colon-separated triple
 * ``hostname,site-collection-guid,site-guid``. Users typically copy
 * this from Graph Explorer or from the response of
 * ``GET /sites/{hostname}:/sites/{site-name}``. We don't auto-resolve
 * by URL in v0.16.0 — keeping the form lean rather than building a
 * site-search picker on first ship.
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
import type { FieldsProps, SharePointConfig } from "../sourceTypes";

export function SharePointFields({
  value,
  onChange,
  errors,
  onFieldBlur,
}: FieldsProps<SharePointConfig>) {
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
            Connect a Microsoft account with read access to the SharePoint
            site you want to index.
          </p>
          <Button onClick={handleSignIn} disabled={busy || start.isPending}>
            {busy || start.isPending ? "Opening…" : "Sign in with Microsoft"}
          </Button>
          <p className="text-[11px] text-fg-muted mt-2">
            Requires a Microsoft OAuth client configured under Settings →
            OAuth providers (Azure App Registration with Sites.Read.All +
            offline_access).
          </p>
        </div>
      )}

      <Input
        label="Site ID"
        value={value.site_id ?? ""}
        onChange={(e) => onChange({ ...value, site_id: e.target.value })}
        placeholder="contoso.sharepoint.com,…,…"
        required
        error={errors?.site_id}
        onBlur={() => onFieldBlur?.("site_id")}
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Graph site identifier — paste the colon-triple from Graph
        Explorer or from the result of <code>GET /sites/&lt;hostname&gt;:/sites/&lt;site-name&gt;</code>.
      </p>

      <Input
        label="Drive ID (optional)"
        value={value.drive_id ?? ""}
        onChange={(e) => onChange({ ...value, drive_id: e.target.value })}
        placeholder="(leave empty for the site's default document library)"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Sites with multiple document libraries — set this to the
        specific drive id. Empty walks the default library.
      </p>

      <Input
        label="Item ID (optional)"
        value={value.item_id ?? ""}
        onChange={(e) => onChange({ ...value, item_id: e.target.value })}
        placeholder="(leave empty to scan the whole library)"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Folder item id to scope the scan within the selected drive.
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
