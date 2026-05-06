import type { ReactNode } from "react";
import { Input, Select } from "../../ui";
import {
  S3_PRESETS,
  type NfsAuthMethod,
  type NfsConfig,
  type S3Config,
  type S3Preset,
  type SmbConfig,
  type SshConfig,
} from "../sourceTypes";

export type HostType = "ssh" | "smb" | "nfs" | "s3";
export type HostConfig = Partial<SshConfig | SmbConfig | NfsConfig | S3Config>;

interface Props {
  type: HostType;
  value: HostConfig;
  onChange: (next: HostConfig) => void;
  /**
   * v0.5.9 — when true, hide the credential-shaped subset
   * (username/password/key fields). The caller is supplying credentials
   * from an attached CredentialProfile and inline credential inputs
   * would be redundant + confusing. Host-shape fields (host/port/
   * known_hosts_path/etc.) keep rendering.
   */
  omitCredentials?: boolean;
}

/**
 * Host-only fields per type. Mirrors the legacy SourceFieldSet but
 * strips share-shaped keys (`share`, `export_path`, `bucket`,
 * `mount_options`, …) — those live on the Source row instead.
 *
 * Single dispatch component (vs. four separate files) because each
 * branch is tight enough to keep in one place, and the coupling
 * between e.g. NfsHostFields and SshHostFields is purely visual.
 */
export function HostFields({ type, value, onChange, omitCredentials }: Props): ReactNode {
  switch (type) {
    case "ssh":
      return <SshHostFields value={value as Partial<SshConfig>} onChange={onChange} omitCredentials={omitCredentials} />;
    case "smb":
      return <SmbHostFields value={value as Partial<SmbConfig>} onChange={onChange} omitCredentials={omitCredentials} />;
    case "nfs":
      return <NfsHostFields value={value as Partial<NfsConfig>} onChange={onChange} omitCredentials={omitCredentials} />;
    case "s3":
      return <S3HostFields value={value as Partial<S3Config>} onChange={onChange} omitCredentials={omitCredentials} />;
  }
}

function SshHostFields({
  value,
  onChange,
  omitCredentials,
}: {
  value: Partial<SshConfig>;
  onChange: (next: Partial<SshConfig>) => void;
  omitCredentials?: boolean;
}) {
  const auth = value.auth ?? "password";
  return (
    <div className="space-y-3">
      <Input
        label="Host"
        value={value.host ?? ""}
        onChange={(e) => onChange({ ...value, host: e.target.value })}
        placeholder="ssh.example.com"
        required
      />
      <Input
        label="Port"
        type="number"
        value={value.port ?? 22}
        onChange={(e) => onChange({ ...value, port: parseInt(e.target.value, 10) || 22 })}
      />
      {!omitCredentials && (
        <>
          <Input
            label="Username"
            value={value.username ?? ""}
            onChange={(e) => onChange({ ...value, username: e.target.value })}
            required
          />
          <Select
            label="Authentication"
            value={auth}
            onChange={(e) =>
              onChange({ ...value, auth: e.target.value as "password" | "key" })
            }
            options={[
              { value: "password", label: "Password" },
              { value: "key", label: "Private key" },
            ]}
          />
          {auth === "password" ? (
            <Input
              label="Password"
              type="password"
              value={value.password === "***" ? "" : (value.password ?? "")}
              onChange={(e) => onChange({ ...value, password: e.target.value })}
              placeholder={value.password === "***" ? "(unchanged — type to replace)" : ""}
              hint={value.password === "***" ? "Existing value preserved. Type a new value to replace it." : undefined}
              required={value.password !== "***"}
            />
          ) : (
            <>
              <Input
                label="Private key path"
                value={value.key_path ?? ""}
                onChange={(e) => onChange({ ...value, key_path: e.target.value })}
                placeholder="/etc/akashic/keys/id_rsa"
                required
              />
              <Input
                label="Key passphrase (optional)"
                type="password"
                value={value.key_passphrase === "***" ? "" : (value.key_passphrase ?? "")}
                onChange={(e) => onChange({ ...value, key_passphrase: e.target.value })}
                placeholder={value.key_passphrase === "***" ? "(unchanged)" : ""}
              />
            </>
          )}
        </>
      )}
      <Input
        label="Known hosts path"
        value={value.known_hosts_path ?? ""}
        onChange={(e) => onChange({ ...value, known_hosts_path: e.target.value })}
        placeholder="/etc/ssh/known_hosts"
        required
      />
    </div>
  );
}

function SmbHostFields({
  value,
  onChange,
  omitCredentials,
}: {
  value: Partial<SmbConfig>;
  onChange: (next: Partial<SmbConfig>) => void;
  omitCredentials?: boolean;
}) {
  return (
    <div className="space-y-3">
      <Input
        label="Host"
        value={value.host ?? ""}
        onChange={(e) => onChange({ ...value, host: e.target.value })}
        placeholder="fileserver.corp.example.com"
        required
      />
      <Input
        label="Port"
        type="number"
        value={value.port ?? 445}
        onChange={(e) => onChange({ ...value, port: parseInt(e.target.value, 10) || 445 })}
      />
      {!omitCredentials && (
        <>
          <Input
            label="Username"
            value={value.username ?? ""}
            onChange={(e) => onChange({ ...value, username: e.target.value })}
            required
          />
          <Input
            label="Password"
            type="password"
            value={value.password === "***" ? "" : (value.password ?? "")}
            onChange={(e) => onChange({ ...value, password: e.target.value })}
            placeholder={value.password === "***" ? "(unchanged — type to replace)" : ""}
            hint={value.password === "***" ? "Existing value preserved. Type a new value to replace it." : undefined}
            required={value.password !== "***"}
          />
          <Input
            label="Domain (optional)"
            value={value.domain ?? ""}
            onChange={(e) => onChange({ ...value, domain: e.target.value })}
            placeholder="EXAMPLE"
          />
        </>
      )}
    </div>
  );
}

function NfsHostFields({
  value,
  onChange,
  omitCredentials,
}: {
  value: Partial<NfsConfig>;
  onChange: (next: Partial<NfsConfig>) => void;
  omitCredentials?: boolean;
}) {
  const authMethod: NfsAuthMethod = value.auth_method ?? "sys";
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
      {!omitCredentials && <>
      <Select
        label="Authentication"
        value={authMethod}
        onChange={(e) =>
          onChange({ ...value, auth_method: e.target.value as NfsAuthMethod })
        }
        options={[
          { value: "sys", label: "AUTH_SYS (default)" },
          { value: "krb5", label: "krb5 (auth-only)" },
          { value: "krb5i", label: "krb5i (not yet implemented)" },
          { value: "krb5p", label: "krb5p (not yet implemented)" },
        ]}
      />
      {authMethod !== "sys" && (
        <div className="space-y-3 rounded-md border border-line p-3 bg-app">
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
          <Input
            label="Keytab path"
            value={value.krb5_keytab_path === "***" ? "" : (value.krb5_keytab_path ?? "")}
            onChange={(e) => onChange({ ...value, krb5_keytab_path: e.target.value })}
            placeholder={value.krb5_keytab_path === "***" ? "(unchanged)" : "/etc/akashic/akashic.keytab"}
          />
          <Input
            label="Password"
            type="password"
            value={value.krb5_password === "***" ? "" : (value.krb5_password ?? "")}
            onChange={(e) => onChange({ ...value, krb5_password: e.target.value })}
            placeholder={value.krb5_password === "***" ? "(unchanged)" : ""}
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
      </>}
    </div>
  );
}

function S3HostFields({
  value,
  onChange,
  omitCredentials,
}: {
  value: Partial<S3Config>;
  onChange: (next: Partial<S3Config>) => void;
  omitCredentials?: boolean;
}) {
  // v0.8.1 — same preset detection as S3Fields. Kept inline (instead
  // of imported) because the host-shaped config doesn't carry
  // `path_style` reliably in legacy host rows; falling back via
  // domain-name sniff catches the common providers.
  const presetKey: S3Preset = (() => {
    const endpoint = (value.endpoint ?? "").toLowerCase();
    if (!endpoint) return "aws";
    if (endpoint.includes("amazonaws.com")) return "aws";
    if (endpoint.includes("wasabisys.com")) return "wasabi";
    if (endpoint.includes("backblazeb2.com")) return "backblaze_b2";
    if (value.path_style === true) return "minio";
    return "other";
  })();
  const meta = S3_PRESETS[presetKey];

  function applyPreset(next: S3Preset) {
    const m = S3_PRESETS[next];
    const endpoint = m.endpoint ?? value.endpoint ?? "";
    onChange({ ...value, endpoint, path_style: m.path_style });
  }

  return (
    <div className="space-y-3">
      <Select
        label="Provider preset"
        value={presetKey}
        onChange={(e) => applyPreset(e.target.value as S3Preset)}
        options={(Object.keys(S3_PRESETS) as S3Preset[]).map((k) => ({
          value: k,
          label: S3_PRESETS[k].label,
        }))}
      />
      <p className="text-[11px] text-fg-muted -mt-1">{meta.hint}</p>

      <Input
        label={presetKey === "aws" ? "Endpoint (optional, AWS uses default)" : "Endpoint"}
        value={value.endpoint ?? ""}
        onChange={(e) => onChange({ ...value, endpoint: e.target.value })}
        placeholder={meta.endpoint_placeholder ?? "https://s3.us-east-1.amazonaws.com"}
      />
      <Input
        label="Region"
        value={value.region ?? ""}
        onChange={(e) => onChange({ ...value, region: e.target.value })}
        placeholder={meta.region_placeholder}
        required
      />
      {!omitCredentials && (
        <>
          <Input
            label="Access key ID"
            value={value.access_key_id ?? ""}
            onChange={(e) => onChange({ ...value, access_key_id: e.target.value })}
            required
          />
          <Input
            label="Secret access key"
            type="password"
            value={value.secret_access_key === "***" ? "" : (value.secret_access_key ?? "")}
            onChange={(e) => onChange({ ...value, secret_access_key: e.target.value })}
            placeholder={value.secret_access_key === "***" ? "(unchanged — type to replace)" : ""}
            hint={value.secret_access_key === "***" ? "Existing value preserved. Type a new value to replace it." : undefined}
            required={value.secret_access_key !== "***"}
          />
        </>
      )}
      {value.path_style !== undefined && (
        <label className="flex items-center gap-2 text-xs text-fg cursor-pointer">
          <input
            type="checkbox"
            checked={value.path_style}
            onChange={(e) => onChange({ ...value, path_style: e.target.checked })}
          />
          <span>
            Use path-style addressing (preset default:{" "}
            {meta.path_style === undefined
              ? "auto — on when endpoint is set"
              : meta.path_style ? "on" : "off"}
            )
          </span>
        </label>
      )}
    </div>
  );
}

/**
 * Returns null if `cfg` is sufficient to attempt a save for the given
 * host type, or a human-readable reason otherwise. Mirrors
 * validateSourceConfig in sourceTypes.ts but skips the share-shaped
 * fields (validated separately by ShareFields' own validator).
 *
 * v0.5.9 — when `omitCredentials` is true, credential-shaped fields
 * (username/password/key/access keys/krb5 fields) are not required
 * because the caller is supplying them via a CredentialProfile.
 * Host-shape fields (host/port/known_hosts_path) still must be valid.
 */
export function validateHostConfig(
  type: HostType,
  cfg: HostConfig,
  omitCredentials = false,
): string | null {
  const c = cfg as Record<string, unknown>;
  const isStr = (k: string) =>
    typeof c[k] === "string" && (c[k] as string).trim() !== "";

  switch (type) {
    case "ssh": {
      if (!isStr("host")) return "Host is required";
      if (!isStr("known_hosts_path")) return "Known hosts path is required";
      if (!omitCredentials) {
        if (!isStr("username")) return "Username is required";
        const auth = c["auth"];
        if (auth === "password") {
          if (!isStr("password")) return "Password is required";
        } else if (auth === "key") {
          if (!isStr("key_path")) return "Key path is required";
        }
      }
      return null;
    }
    case "smb":
      if (!isStr("host")) return "Host is required";
      if (!omitCredentials) {
        if (!isStr("username")) return "Username is required";
        if (!isStr("password")) return "Password is required";
      }
      return null;
    case "nfs": {
      if (!isStr("host")) return "Host is required";
      if (!omitCredentials) {
        const method = (c["auth_method"] as NfsAuthMethod | undefined) ?? "sys";
        if (method !== "sys") {
          if (!isStr("krb5_principal")) return "Kerberos principal is required";
          if (!isStr("krb5_realm")) return "Kerberos realm is required";
          const isProvided = (k: string) => isStr(k) && c[k] !== "***";
          const hasKeytab = isProvided("krb5_keytab_path");
          const hasPassword = isProvided("krb5_password");
          const hasAny = isStr("krb5_keytab_path") || isStr("krb5_password");
          if (!hasAny) return "Kerberos requires either a keytab path or a password";
          if (hasKeytab && hasPassword) return "Provide either a keytab path or a password, not both";
        }
      }
      return null;
    }
    case "s3":
      if (!isStr("region")) return "Region is required";
      if (!omitCredentials) {
        if (!isStr("access_key_id")) return "Access key ID is required";
        if (!isStr("secret_access_key")) return "Secret access key is required";
      }
      return null;
  }
}
