import { useMemo, useState } from "react";
import { toast } from "sonner";

import {
  PROVIDER_LABELS,
  useUpsertOAuthProvider,
  type OAuthProviderName,
  type OAuthProviderSummary,
} from "../../hooks/useOAuthProviders";
import { Button, Input } from "../ui";

interface Props {
  provider: OAuthProviderName;
  /** Existing config when editing; null when adding a new provider. */
  existing: OAuthProviderSummary | null;
  onSaved: () => void;
  onCancel: () => void;
}

/**
 * Shared client_id / client_secret / redirect_uri form. Used by both
 * the add-provider wizard's step 2 and the edit-existing modal — the
 * fields and validation are identical for all three providers, so the
 * form is provider-agnostic apart from the heading.
 *
 * On edit, the secret field allows blank submission to keep the existing
 * encrypted value (review W-I8 from v0.24.0).
 */
export function ProviderForm({ provider, existing, onSaved, onCancel }: Props) {
  const upsert = useUpsertOAuthProvider();
  const defaultRedirect = useMemo(
    () => `${window.location.origin}/api/oauth/callback`,
    [],
  );
  const [clientId, setClientId] = useState(existing?.client_id ?? "");
  const [clientSecret, setClientSecret] = useState("");
  const [redirectUri, setRedirectUri] = useState(
    existing?.redirect_uri || defaultRedirect,
  );

  async function handleSave() {
    const needsSecret = !existing?.has_secret;
    if (
      !clientId.trim() ||
      !redirectUri.trim() ||
      (needsSecret && !clientSecret.trim())
    ) {
      toast.error(
        needsSecret
          ? "Client ID, client secret, and redirect URI are required."
          : "Client ID and redirect URI are required.",
      );
      return;
    }
    try {
      await upsert.mutateAsync({
        provider,
        body: {
          client_id: clientId.trim(),
          client_secret: clientSecret.trim() || null,
          redirect_uri: redirectUri.trim(),
        },
      });
      toast.success(`${PROVIDER_LABELS[provider]} saved.`);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save provider");
    }
  }

  return (
    <div className="space-y-3">
      <Input
        label="Client ID"
        value={clientId}
        onChange={(e) => setClientId(e.target.value)}
        required
      />
      <Input
        label={existing?.has_secret ? "Client secret (replace)" : "Client secret"}
        type="password"
        value={clientSecret}
        onChange={(e) => setClientSecret(e.target.value)}
        placeholder={existing?.has_secret ? "(unchanged — type to replace)" : ""}
        autoComplete="new-password"
        required={!existing?.has_secret}
      />
      <Input
        label="Redirect URI"
        value={redirectUri}
        onChange={(e) => setRedirectUri(e.target.value)}
        required
      />
      <p className="text-[11px] text-fg-muted">
        Must match the redirect URI registered in your provider's OAuth app
        exactly. Akashic's callback is served at <code>/api/oauth/callback</code>.
      </p>
      <div className="flex items-center justify-end gap-2 pt-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={handleSave} loading={upsert.isPending}>
          Save
        </Button>
      </div>
    </div>
  );
}
