/**
 * WebDAV (v0.11.0) source-config form.
 *
 * Hostless source type. URL points at the share root, plus optional
 * basic auth credentials and a TLS-verify toggle for self-signed
 * installs.
 *
 * Provider hints — the URL pattern varies per implementation:
 *   - Nextcloud: https://nextcloud.example.com/remote.php/dav/files/<user>/
 *   - ownCloud:  https://owncloud.example.com/remote.php/dav/files/<user>/
 *   - Synology File Station: https://nas.example.com:5006/  (DSM proxies WebDAV)
 *   - Apache mod_dav: whatever the operator mounted (e.g. https://files.example.com/data/)
 *   - sabredav: depends on the install's routing config
 *
 * The form keeps copy generic since pasting the URL from the
 * provider's "WebDAV setup" docs is the canonical workflow.
 */
import { useState } from "react";
import { Button, Input } from "../../ui";
import type { FieldsProps, WebDAVConfig } from "../sourceTypes";

export function WebDAVFields({ value, onChange }: FieldsProps<WebDAVConfig>) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const tlsVerify = value.tls_verify ?? true;

  return (
    <div className="space-y-3">
      <Input
        label="URL"
        value={value.url ?? ""}
        onChange={(e) => onChange({ ...value, url: e.target.value })}
        placeholder="https://nextcloud.example.com/remote.php/dav/files/admin/"
        required
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Point at the share root the user has access to. Nextcloud /
        ownCloud append <code>/remote.php/dav/files/&lt;user&gt;/</code>;
        Synology DSM / generic mod_dav use whatever the operator
        mounted.
      </p>

      <Input
        label="Username (optional)"
        value={value.username ?? ""}
        onChange={(e) => onChange({ ...value, username: e.target.value })}
        placeholder="admin"
      />
      <Input
        label="Password (optional)"
        type="password"
        value={value.password === "***" ? "" : (value.password ?? "")}
        onChange={(e) => onChange({ ...value, password: e.target.value })}
        placeholder={
          value.password === "***" ? "(unchanged — type to replace)" : ""
        }
        autoComplete="new-password"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Sent over HTTP Basic Auth. Use a Nextcloud / ownCloud
        app-specific password (Settings → Security → App passwords)
        rather than the user's main password where possible — easier
        to revoke.
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
            On by default. Uncheck for self-signed home installs on
            trusted networks.
          </p>
          {!tlsVerify && (
            <div className="rounded-md border border-amber-300 bg-amber-50 dark:border-amber-700/40 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
              TLS verification is off. The scanner will accept any
              certificate, including ones signed by an attacker on
              the network path. Only acceptable for trusted-network
              home installs.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
