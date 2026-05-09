import { useMemo, useState } from "react";

import {
  PROVIDER_LABELS,
  type OAuthProviderName,
  type OAuthProviderSummary,
} from "../../hooks/useOAuthProviders";
import { Button, ModalShell } from "../ui";
import { ProviderForm } from "./ProviderForm";

interface Props {
  /** All known providers from the backend (configured + not). */
  providers: OAuthProviderSummary[];
  onClose: () => void;
}

/** Where each provider's OAuth app gets registered — surfaced as a
 *  hint link next to the picker option so admins know where to go. */
const CONSOLE_LINKS: Record<OAuthProviderName, { label: string; href: string }> = {
  google: {
    label: "Google Cloud Console",
    href: "https://console.cloud.google.com/apis/credentials",
  },
  microsoft: {
    label: "Azure App registrations",
    href: "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
  },
  dropbox: {
    label: "Dropbox app console",
    href: "https://www.dropbox.com/developers/apps",
  },
};

/**
 * Two-step modal for adding a not-yet-configured OAuth provider:
 *   1. Picker: provider type (Google / Microsoft / Dropbox), filtered
 *      to those without a stored client_id/secret.
 *   2. Form: shared `ProviderForm` (client_id, client_secret, redirect_uri).
 *
 * Replaces the previous always-three-rows layout where unconfigured
 * providers nudged the user with a "Configure" button regardless of
 * intent. Only configured providers appear on the page now.
 */
export function AddProviderWizard({ providers, onClose }: Props) {
  const available = useMemo(
    () => providers.filter((p) => !p.has_secret || p.client_id === ""),
    [providers],
  );
  const [picked, setPicked] = useState<OAuthProviderName | null>(null);

  if (available.length === 0) {
    return (
      <ModalShell open onClose={onClose} maxWidth="md" ariaLabelledBy="add-oauth-empty-title">
        <div className="p-5 space-y-3">
          <h2 id="add-oauth-empty-title" className="text-base font-semibold text-fg">
            All providers configured
          </h2>
          <p className="text-sm text-fg-muted">
            Every supported OAuth provider already has a client app stored.
            Use the rows on the page to edit or remove an existing config.
          </p>
          <div className="flex items-center justify-end pt-2">
            <Button onClick={onClose}>Close</Button>
          </div>
        </div>
      </ModalShell>
    );
  }

  if (picked === null) {
    return (
      <ModalShell open onClose={onClose} maxWidth="md" ariaLabelledBy="add-oauth-pick-title">
        <div className="p-5 space-y-3">
          <div>
            <p className="text-xs text-fg-muted">Settings → OAuth providers</p>
            <h2 id="add-oauth-pick-title" className="text-base font-semibold text-fg">
              Add OAuth provider
            </h2>
          </div>
          <p className="text-xs text-fg-muted">
            Pick a provider, then paste the credentials from your OAuth app
            registration.
          </p>
          <div role="radiogroup" aria-label="Provider type" className="space-y-2">
            {available.map((p) => {
              const console = CONSOLE_LINKS[p.provider];
              return (
                <label
                  key={p.provider}
                  className="flex items-start gap-3 rounded-md border border-line p-3 cursor-pointer hover:bg-surface-muted focus-within:ring-2 focus-within:ring-accent-500"
                >
                  <input
                    type="radio"
                    name="oauth-provider"
                    className="mt-1 h-4 w-4 text-accent-600 focus:ring-accent-400"
                    onChange={() => setPicked(p.provider)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-fg">
                      {PROVIDER_LABELS[p.provider]}
                    </div>
                    <div className="text-[11px] text-fg-muted mt-0.5">
                      Register an app at{" "}
                      <a
                        href={console.href}
                        target="_blank"
                        rel="noreferrer"
                        className="underline hover:text-fg"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {console.label}
                      </a>{" "}
                      and paste the client_id / client_secret next.
                    </div>
                  </div>
                </label>
              );
            })}
          </div>
          <div className="flex items-center justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          </div>
        </div>
      </ModalShell>
    );
  }

  return (
    <ModalShell open onClose={onClose} maxWidth="md" ariaLabelledBy="add-oauth-form-title">
      <div className="p-5 space-y-3">
        <div>
          <p className="text-xs text-fg-muted">Settings → OAuth providers</p>
          <h2 id="add-oauth-form-title" className="text-base font-semibold text-fg">
            {PROVIDER_LABELS[picked]} OAuth app
          </h2>
        </div>
        <p className="text-xs text-fg-muted">
          Paste the credentials from your provider's developer console.
          The secret is encrypted at rest with a key derived from{" "}
          <code>AKASHIC_SECRET_KEY</code>.
        </p>
        <ProviderForm
          provider={picked}
          existing={null}
          onSaved={onClose}
          onCancel={() => setPicked(null)}
        />
      </div>
    </ModalShell>
  );
}
