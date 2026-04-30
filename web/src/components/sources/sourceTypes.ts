export const SOURCE_TYPES = ["local", "ssh", "smb", "nfs", "s3"] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  local: "Local filesystem",
  ssh: "SSH / SFTP",
  smb: "SMB / CIFS",
  nfs: "NFS",
  s3: "S3-compatible",
};

export type LocalConfig = {
  path: string;
};

export type NfsAuthMethod = "sys" | "krb5" | "krb5i" | "krb5p";

export type NfsConfig = {
  host: string;
  export_path: string;
  mount_options?: string;
  // Phase 3b — AUTH_SYS identity to present to the server. Defaults
  // (uid=0, gid=0, no aux GIDs) work for most exports configured with
  // `no_root_squash` or readable by anyone. Servers with `root_squash`
  // (the Linux default) need a non-root uid here.
  port?: number;
  auth_uid?: number;
  auth_gid?: number;
  auth_aux_gids?: number[];
  // Per-probe timeout in seconds, [1, 60]. Empty/zero = use scanner
  // default (5s). Useful when the server lives across a slow link.
  probe_timeout_seconds?: number;
  // Phase 3c — Kerberos / RPCSEC_GSS. Only consulted when auth_method is
  // krb5/krb5i/krb5p. krb5i and krb5p are accepted as values but the
  // current scanner build implements only sec=krb5 (auth-only); the
  // other two surface as a config-step error from the test endpoint.
  auth_method?: NfsAuthMethod;
  krb5_principal?: string;
  krb5_realm?: string;
  // SPN; defaults to "nfs/<host>" when empty.
  krb5_service_principal?: string;
  // Path to a keytab on the scanner host; mutually exclusive with password.
  krb5_keytab_path?: string;
  // Password — sent over stdin to the scanner so it never appears in argv.
  krb5_password?: string;
  // Alternate krb5.conf path; default /etc/krb5.conf with DNS fallback.
  krb5_config_path?: string;
};

export type SshConfig = {
  host: string;
  port?: number;
  username: string;
  auth: "password" | "key";
  password?: string;
  key_path?: string;
  key_passphrase?: string;
  known_hosts_path: string;
};

export type SmbConfig = {
  host: string;
  port?: number;
  username: string;
  password: string;
  share: string;
  domain?: string;
};

export type S3Config = {
  endpoint?: string;
  bucket: string;
  region: string;
  access_key_id: string;
  secret_access_key: string;
};

export type AnyConfig =
  | LocalConfig
  | NfsConfig
  | SshConfig
  | SmbConfig
  | S3Config;

export interface FieldsProps<C> {
  value: Partial<C>;
  onChange: (next: Partial<C>) => void;
}

/**
 * Returns null if `cfg` is sufficient to attempt a save for the given type,
 * or a human-readable reason if a required field is missing. Used to disable
 * the Save button until the form is minimally valid.
 */
export function validateSourceConfig(
  type: SourceType,
  cfg: Partial<AnyConfig>,
): string | null {
  const c = cfg as Record<string, unknown>;
  const isStr = (k: string) => typeof c[k] === "string" && (c[k] as string).trim() !== "";

  switch (type) {
    case "local":
      return isStr("path") ? null : "Path is required";
    case "nfs": {
      if (!isStr("host")) return "Host is required";
      if (!isStr("export_path")) return "Export path is required";
      const method = (c["auth_method"] as NfsAuthMethod | undefined) ?? "sys";
      if (method !== "sys") {
        if (!isStr("krb5_principal")) return "Kerberos principal is required";
        if (!isStr("krb5_realm")) return "Kerberos realm is required";
        // "***" is the API's masked-secret sentinel — it represents "the
        // saved value is being preserved", not "the user entered ***".
        // Treat it as no-input for either-or validation so a user editing
        // a saved keytab-auth source can switch to password (or vice
        // versa) by typing into one field while the other still displays
        // the masked sentinel.
        const isProvided = (k: string) =>
          isStr(k) && c[k] !== "***";
        const hasKeytab = isProvided("krb5_keytab_path");
        const hasPassword = isProvided("krb5_password");
        // Either field having ANY value (provided or sentinel) keeps the
        // user covered — they're either saving a new value or preserving
        // an existing one.
        const hasAny =
          isStr("krb5_keytab_path") || isStr("krb5_password");
        if (!hasAny) {
          return "Kerberos requires either a keytab path or a password";
        }
        if (hasKeytab && hasPassword) {
          return "Provide either a keytab path or a password, not both";
        }
      }
      return null;
    }
    case "ssh": {
      if (!isStr("host")) return "Host is required";
      if (!isStr("username")) return "Username is required";
      if (!isStr("known_hosts_path")) return "Known hosts path is required";
      const auth = c["auth"];
      if (auth === "password" && !isStr("password"))
        return "Password is required";
      if (auth === "key" && !isStr("key_path"))
        return "Key path is required";
      return null;
    }
    case "smb":
      if (!isStr("host")) return "Host is required";
      if (!isStr("username")) return "Username is required";
      if (!isStr("password")) return "Password is required";
      if (!isStr("share")) return "Share is required";
      return null;
    case "s3":
      if (!isStr("bucket")) return "Bucket is required";
      if (!isStr("region")) return "Region is required";
      if (!isStr("access_key_id")) return "Access key ID is required";
      if (!isStr("secret_access_key")) return "Secret access key is required";
      return null;
  }
}
