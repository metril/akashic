import { useEffect, useRef, useState } from "react";
import { Input } from "../../ui";
import type { FieldsProps, NfsAuthMethod, NfsConfig } from "../sourceTypes";

const AUTH_METHOD_OPTIONS: ReadonlyArray<{ id: NfsAuthMethod; label: string; help: string }> = [
  { id: "sys", label: "AUTH_SYS", help: "Default. UID/GID-based identity over plain RPC." },
  { id: "krb5", label: "krb5", help: "Kerberos auth-only (RPCSEC_GSS, service=none)." },
  { id: "krb5i", label: "krb5i", help: "Kerberos with integrity (MIC). Not yet implemented in scanner." },
  { id: "krb5p", label: "krb5p", help: "Kerberos with privacy (encrypted args). Not yet implemented in scanner." },
];

export function NfsFields({ value, onChange }: FieldsProps<NfsConfig>) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const authMethod: NfsAuthMethod = value.auth_method ?? "sys";
  const showKrb5 = authMethod !== "sys";

  // Aux GIDs use a local string state so the user can type a trailing
  // comma without the parsed-and-rejoined display snapping back over
  // their cursor. We sync to the parent (number[]) on blur, which is
  // the natural commit point for this kind of free-form list input.
  const externalAuxString = (value.auth_aux_gids ?? []).join(", ");
  const [auxText, setAuxText] = useState(externalAuxString);
  const lastSyncedExternal = useRef(externalAuxString);
  useEffect(() => {
    // Re-seed local state ONLY when the external value actually changes
    // (e.g., the parent reloaded a saved source). Don't fight the user
    // mid-typing.
    if (externalAuxString !== lastSyncedExternal.current) {
      setAuxText(externalAuxString);
      lastSyncedExternal.current = externalAuxString;
    }
  }, [externalAuxString]);

  function commitAuxText(s: string) {
    const parsed = s
      .split(",")
      .map((p) => p.trim())
      .filter((p) => p !== "")
      .map((p) => Number(p))
      .filter((n) => Number.isFinite(n) && n >= 0);
    onChange({ ...value, auth_aux_gids: parsed });
    lastSyncedExternal.current = parsed.join(", ");
  }

  return (
    <div className="space-y-3">
      <Input
        label="Host"
        value={value.host ?? ""}
        onChange={(e) => onChange({ ...value, host: e.target.value })}
        placeholder="nfs.example.com"
        required
      />
      <Input
        label="Export path"
        value={value.export_path ?? ""}
        onChange={(e) => onChange({ ...value, export_path: e.target.value })}
        placeholder="/srv/nfs/data"
        required
      />
      <div>
        <label className="block text-xs font-medium text-fg mb-1">
          Authentication
        </label>
        <div className="flex flex-wrap gap-2">
          {AUTH_METHOD_OPTIONS.map((opt) => (
            <label
              key={opt.id}
              className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs cursor-pointer ${
                authMethod === opt.id
                  ? "border-blue-500 bg-blue-50 text-blue-900 dark:text-blue-200"
                  : "border-line bg-surface text-fg hover:bg-surface-muted"
              }`}
              title={opt.help}
            >
              <input
                type="radio"
                name="nfs-auth-method"
                value={opt.id}
                checked={authMethod === opt.id}
                onChange={() => onChange({ ...value, auth_method: opt.id })}
                className="accent-blue-600"
              />
              <span>{opt.label}</span>
            </label>
          ))}
        </div>
        <p className="text-[11px] text-fg-muted mt-1">
          Most exports use AUTH_SYS. Use krb5 when the export is configured
          with <code>sec=krb5</code> in <code>/etc/exports</code>.
        </p>
      </div>

      {showKrb5 && (
        <div className="space-y-3 rounded-md border border-line p-3 bg-app">
          <p className="text-[11px] text-fg-muted">
            Kerberos auth uses RPCSEC_GSS over NFSv4 only. The scanner host
            must reach the KDC and the NFS server. Provide either a keytab
            file <em>on the scanner host</em> or a password (sent over stdin,
            not argv).
            {(authMethod === "krb5i" || authMethod === "krb5p") && (
              <span className="block mt-1 text-amber-700">
                Note: {authMethod} is not yet implemented in this build —
                only sec=krb5 (auth-only) works end-to-end. Saving is
                allowed; the test button will report a config error.
              </span>
            )}
          </p>
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Principal (no @realm)"
              value={value.krb5_principal ?? ""}
              onChange={(e) => onChange({ ...value, krb5_principal: e.target.value })}
              placeholder="akashic-svc"
              required
            />
            <Input
              label="Realm"
              value={value.krb5_realm ?? ""}
              onChange={(e) => onChange({ ...value, krb5_realm: e.target.value })}
              placeholder="EXAMPLE.COM"
              required
            />
          </div>
          <Input
            label="Service principal (optional)"
            value={value.krb5_service_principal ?? ""}
            onChange={(e) => onChange({ ...value, krb5_service_principal: e.target.value })}
            placeholder={`nfs/${value.host || "<host>"}`}
          />
          <p className="text-[11px] text-fg-muted -mt-1">
            Defaults to <code>nfs/&lt;host&gt;</code>. Override only if the
            server uses a non-standard SPN.
          </p>
          <Input
            label="Keytab path on scanner host"
            // The schema's _scrub_config masks any field whose name
            // contains "key" — including this path. When the API
            // returns "***" we render an empty field with a "preserve
            // existing" placeholder rather than literally showing
            // "***", which would let the user accidentally save the
            // sentinel as a value.
            value={value.krb5_keytab_path === "***" ? "" : (value.krb5_keytab_path ?? "")}
            onChange={(e) => onChange({ ...value, krb5_keytab_path: e.target.value })}
            placeholder={
              value.krb5_keytab_path === "***"
                ? "(unchanged — type to replace)"
                : "/etc/akashic/akashic.keytab"
            }
          />
          <Input
            label="Password"
            type="password"
            value={value.krb5_password === "***" ? "" : (value.krb5_password ?? "")}
            onChange={(e) => onChange({ ...value, krb5_password: e.target.value })}
            placeholder="(unchanged — type to replace)"
            autoComplete="new-password"
          />
          <p className="text-[11px] text-fg-muted -mt-1">
            Provide a keytab path <em>or</em> a password, not both.
          </p>
          <Input
            label="krb5.conf path (optional)"
            value={value.krb5_config_path ?? ""}
            onChange={(e) => onChange({ ...value, krb5_config_path: e.target.value })}
            placeholder="/etc/krb5.conf"
          />
        </div>
      )}

      <button
        type="button"
        onClick={() => setShowAdvanced((s) => !s)}
        className="text-xs text-fg-muted hover:text-fg underline"
      >
        {showAdvanced ? "Hide" : "Show"} advanced options
      </button>
      {showAdvanced && (
        <div className="space-y-3 rounded-md border border-line p-3">
          <Input
            label="Port"
            type="number"
            value={value.port?.toString() ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                onChange({ ...value, port: undefined });
                return;
              }
              const n = Number(raw);
              if (Number.isFinite(n) && n >= 1 && n <= 65535) {
                onChange({ ...value, port: n });
              }
            }}
            placeholder="2049"
          />
          <Input
            label="Mount options"
            value={value.mount_options ?? ""}
            onChange={(e) => onChange({ ...value, mount_options: e.target.value })}
            placeholder="vers=4.1,sec=sys"
          />

          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Auth UID"
              type="number"
              value={(value.auth_uid ?? 0).toString()}
              onChange={(e) => {
                const n = Number(e.target.value);
                onChange({ ...value, auth_uid: Number.isFinite(n) && n >= 0 ? n : 0 });
              }}
              placeholder="0"
            />
            <Input
              label="Auth GID"
              type="number"
              value={(value.auth_gid ?? 0).toString()}
              onChange={(e) => {
                const n = Number(e.target.value);
                onChange({ ...value, auth_gid: Number.isFinite(n) && n >= 0 ? n : 0 });
              }}
              placeholder="0"
            />
          </div>
          <p className="text-[11px] text-fg-muted -mt-1">
            UID/GID are presented as the AUTH_SYS identity to the NFS server.
            For exports configured with <code>root_squash</code> (the Linux
            default), use a non-root UID that has access to the share.
          </p>

          <Input
            label="Aux GIDs"
            value={auxText}
            onChange={(e) => setAuxText(e.target.value)}
            onBlur={() => commitAuxText(auxText)}
            placeholder="27, 100"
          />
          <p className="text-[11px] text-fg-muted -mt-1">
            Comma-separated supplementary group IDs (max 16). Useful when the
            export is restricted to a particular group.
          </p>

          <Input
            label="Probe timeout (seconds)"
            type="number"
            value={value.probe_timeout_seconds?.toString() ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                onChange({ ...value, probe_timeout_seconds: undefined });
                return;
              }
              const n = Number(raw);
              if (Number.isFinite(n) && n >= 1 && n <= 60) {
                onChange({ ...value, probe_timeout_seconds: n });
              }
              // Out-of-range / NaN: drop the keystroke; the field
              // shows the previously-valid value. Inline error UI is
              // a future improvement; for now the constraint is also
              // server-validated.
            }}
            placeholder="5"
          />
          <p className="text-[11px] text-fg-muted -mt-1">
            Per-RPC timeout, 1–60 seconds (default 5). Raise this when the
            server is across a slow or congested link.
          </p>
        </div>
      )}
      <p className="text-xs text-fg-muted bg-app rounded-md p-2">
        {authMethod === "sys" ? (
          <>
            Test probes the export end-to-end via MOUNT3 / NFSv4 with AUTH_SYS.
            The success indicator below tells you which protocol path validated
            the mount; a "tcp" tier means the server is reachable but neither
            v3 nor v4 could be fully validated.
          </>
        ) : (
          <>
            Kerberos test runs a TGS_REQ to the KDC, then NFSv4 LOOKUP under
            RPCSEC_GSS — MOUNT3 is skipped because most exports configured for
            sec=krb5 don't expose mountd over GSS. Failures attribute to{" "}
            <code>auth</code> (KDC, ticket, or context establishment),{" "}
            <code>connect</code> (server unreachable), or <code>list</code>{" "}
            (export path missing).
          </>
        )}
      </p>
    </div>
  );
}
