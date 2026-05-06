/**
 * Paperless-ngx (v0.7.0) source-config form.
 *
 * Hostless source type. Three required fields: URL, API token (masked
 * as "***" by the api on round-trip), and an optional tag whitelist.
 * The TLS-verify toggle is exposed under "Show advanced options" since
 * most users won't need it; flipping it off shows a warning banner so
 * the gap is intentional.
 *
 * Auth note: Paperless uses `Authorization: Token <api_token>` (NOT
 * `Bearer`). The user creates the token in Paperless via the user
 * dropdown → "My Profile" → "Create Auth Token".
 */
import { useState } from "react";
import { Button, Input, MaskedInput } from "../../ui";
import type { FieldsProps, PaperlessConfig } from "../sourceTypes";

export function PaperlessFields({ value, onChange, errors, onFieldBlur }: FieldsProps<PaperlessConfig>) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const tlsVerify = value.tls_verify ?? true;

  return (
    <div className="space-y-3">
      <Input
        label="URL"
        value={value.url ?? ""}
        onChange={(e) => onChange({ ...value, url: e.target.value })}
        placeholder="https://paperless.example.com"
        required
        error={errors?.url}
        onBlur={() => onFieldBlur?.("url")}
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Base URL of the Paperless-ngx instance. The scanner walks{" "}
        <code>/api/documents/</code> from here.
      </p>

      <MaskedInput
        label="API token"
        value={value.api_token === "***" ? "" : (value.api_token ?? "")}
        onChange={(e) => onChange({ ...value, api_token: e.target.value })}
        placeholder={
          value.api_token === "***"
            ? "(unchanged — type to replace)"
            : ""
        }
        autoComplete="new-password"
        required={value.api_token !== "***"}
        error={errors?.api_token}
        onBlur={() => onFieldBlur?.("api_token")}
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Create a token in Paperless under <em>My Profile → Create Auth Token</em>.
        Paperless uses the literal scheme "Token" (not "Bearer").
      </p>

      <Input
        label="Tag whitelist (optional)"
        value={value.tag_filter ?? ""}
        onChange={(e) => onChange({ ...value, tag_filter: e.target.value })}
        placeholder="tax, invoice, archived"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Comma-separated. When set, only documents carrying at least one
        of these tags are indexed. Leave blank to index every document.
      </p>

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
            On by default. Uncheck for self-signed paperless installs on
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
