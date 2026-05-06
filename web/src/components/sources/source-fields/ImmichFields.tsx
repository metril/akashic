/**
 * Immich (v0.8.0) source-config form.
 *
 * Hostless source type. URL + API key + optional album whitelist +
 * "include archived" toggle. The TLS-verify toggle lives under
 * "advanced options" since most users won't need it; flipping it off
 * surfaces a warning so the trust gap is intentional.
 *
 * Auth note: Immich uses `x-api-key: <key>` (NOT `Authorization:
 * Bearer`). Users create a key in Immich under Account Settings →
 * API Keys. The key inherits the user's permissions, so it's worth
 * creating a dedicated "akashic" key per install for clean
 * audit-log attribution and easy revocation.
 */
import { useState } from "react";
import { Button, Input } from "../../ui";
import type { FieldsProps, ImmichConfig } from "../sourceTypes";

export function ImmichFields({ value, onChange }: FieldsProps<ImmichConfig>) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const tlsVerify = value.tls_verify ?? true;
  const includeArchived = value.include_archived ?? false;

  return (
    <div className="space-y-3">
      <Input
        label="URL"
        value={value.url ?? ""}
        onChange={(e) => onChange({ ...value, url: e.target.value })}
        placeholder="https://immich.example.com"
        required
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Base URL of the Immich instance. The scanner walks{" "}
        <code>/api/search/metadata</code> from here.
      </p>

      <Input
        label="API key"
        type="password"
        value={value.api_key === "***" ? "" : (value.api_key ?? "")}
        onChange={(e) => onChange({ ...value, api_key: e.target.value })}
        placeholder={
          value.api_key === "***" ? "(unchanged — type to replace)" : ""
        }
        autoComplete="new-password"
        required={value.api_key !== "***"}
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Create a key in Immich under <em>Account Settings → API Keys</em>.
        The key inherits the owning user's permissions; consider making
        a dedicated read-only "akashic" key for clean audit attribution.
      </p>

      <Input
        label="Album whitelist (optional)"
        value={value.album_filter ?? ""}
        onChange={(e) => onChange({ ...value, album_filter: e.target.value })}
        placeholder="Vacation, Family, Pets"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Comma-separated album names. When set, only assets in at least
        one of these albums are indexed. Leave blank to index every
        asset.
      </p>

      <label className="flex items-start gap-2 text-sm text-fg cursor-pointer select-none">
        <input
          type="checkbox"
          checked={includeArchived}
          onChange={(e) => onChange({ ...value, include_archived: e.target.checked })}
          className="mt-0.5 h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
        />
        <span>
          <span className="font-medium">Include archived assets</span>
          <span className="block text-xs text-fg-muted mt-0.5">
            Off by default — matches Immich's UI behaviour where
            archived assets stay hidden from the photo grid. Turn on
            to index them anyway.
          </span>
        </span>
      </label>

      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={() => setShowAdvanced((s) => !s)}
      >
        {showAdvanced ? "Hide" : "Show"} advanced options
      </Button>
      {showAdvanced && (
        <div className="space-y-2 rounded-md border border-line p-3">
          <label className="flex items-center gap-2 text-xs text-fg cursor-pointer">
            <input
              type="checkbox"
              checked={tlsVerify}
              onChange={(e) =>
                onChange({ ...value, tls_verify: e.target.checked })
              }
            />
            <span>Verify TLS certificate</span>
          </label>
          <p className="text-[11px] text-fg-muted">
            On by default. Uncheck for self-signed Immich installs on
            home networks.
          </p>
          {!tlsVerify && (
            <div className="rounded-md border border-amber-300 bg-amber-50 dark:border-amber-700/40 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
              TLS verification is off. The scanner will accept any
              certificate, including ones signed by an attacker on the
              network path. Only acceptable for trusted-network home
              installs.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
