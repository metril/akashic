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

export type NfsConfig = {
  host: string;
  export_path: string;
  mount_options?: string;
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
    case "nfs":
      if (!isStr("host")) return "Host is required";
      if (!isStr("export_path")) return "Export path is required";
      return null;
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
