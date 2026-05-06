export const SOURCE_TYPES = [
  "local", "ssh", "smb", "nfs", "s3",
  "paperless", "immich", "azureblob", "gcs", "webdav",
  "gdrive", "onedrive", "sharepoint", "dropbox", "box",
] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

// v0.7.0 — source types that don't attach to a Host row. The source
// carries the URL/credentials directly in connection_config. Mirrors
// HOSTLESS_SOURCE_TYPES on the api side.
export const HOSTLESS_SOURCE_TYPES: ReadonlySet<SourceType> = new Set([
  "local",
  "paperless",
  "immich",
  "azureblob",
  "gcs",
  "webdav",
  "gdrive",
  "onedrive",
  "sharepoint",
  "dropbox",
  "box",
]);

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  local: "Local filesystem",
  ssh: "SSH / SFTP",
  smb: "SMB / CIFS",
  nfs: "NFS",
  s3: "S3-compatible",
  paperless: "Paperless-ngx",
  immich: "Immich",
  azureblob: "Azure Blob Storage",
  gcs: "Google Cloud Storage",
  webdav: "WebDAV (Nextcloud / ownCloud / Synology / mod_dav)",
  gdrive: "Google Drive",
  onedrive: "OneDrive (Microsoft 365)",
  sharepoint: "SharePoint document library",
  dropbox: "Dropbox",
  box: "Box",
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
  // v0.8.1 — explicit override for the S3 SDK's UsePathStyle. Omit
  // for auto (path-style when endpoint is set, virtual-hosted
  // otherwise — works for AWS + MinIO). Set to false for Wasabi /
  // Backblaze B2 (their endpoint URLs want virtual-hosted-style),
  // or true for AWS-shaped URLs behind a reverse proxy that needs
  // path-style routing.
  path_style?: boolean;
};

// v0.8.1 — S3-compatible storage providers we ship a one-click preset
// for. The preset prefills endpoint + path_style; the user still
// needs to fill region + bucket + credentials. "other" is the legacy
// "fill the endpoint by hand" path (no defaults applied beyond auto
// path_style).
export type S3Preset = "aws" | "minio" | "wasabi" | "backblaze_b2" | "other";

export interface S3PresetDefaults {
  endpoint?: string;
  path_style?: boolean;
  region_placeholder: string;
  endpoint_placeholder?: string;
  // Short blurb shown under the preset dropdown.
  hint: string;
}

export const S3_PRESETS: Record<S3Preset, S3PresetDefaults & { label: string }> = {
  aws: {
    label: "AWS S3",
    region_placeholder: "us-east-1",
    hint: "Standard AWS. Endpoint blank, virtual-hosted-style addressing.",
  },
  minio: {
    label: "MinIO",
    endpoint_placeholder: "http://minio.local:9000",
    region_placeholder: "us-east-1",
    path_style: true,
    hint: "Requires path-style addressing. Region is decorative — use any value.",
  },
  wasabi: {
    label: "Wasabi",
    endpoint_placeholder: "https://s3.us-east-1.wasabisys.com",
    region_placeholder: "us-east-1",
    path_style: false,
    hint: "Wasabi uses virtual-hosted-style with the regional endpoint. Set the region explicitly.",
  },
  backblaze_b2: {
    label: "Backblaze B2",
    endpoint_placeholder: "https://s3.us-west-002.backblazeb2.com",
    region_placeholder: "us-west-002",
    path_style: false,
    hint: "B2's S3-compatible API uses virtual-hosted-style with the regional endpoint.",
  },
  other: {
    label: "Other / custom",
    region_placeholder: "us-east-1",
    hint: "Fill in endpoint manually. Path-style auto-on when endpoint is set.",
  },
};

// v0.7.0 — Paperless-ngx (Tier 3 self-hosted libraries). Hostless: no
// Host row, all connection fields live on the source. tag_filter is a
// comma-separated whitelist (case-insensitive) — when set, only
// documents carrying at least one of those tags are indexed.
export type PaperlessConfig = {
  url: string;
  api_token: string;
  tag_filter?: string;
  // Default true. Set to false for self-signed home installs; UI
  // surfaces a warning when the toggle is off.
  tls_verify?: boolean;
};

// v0.8.0 — Immich (Tier 3 self-hosted photo / video library).
// Hostless. album_filter is a comma-separated whitelist of album
// NAMES (case-insensitive); empty = index every asset. Archived
// assets are excluded by default to mirror Immich's UI behaviour.
export type ImmichConfig = {
  url: string;
  api_key: string;
  album_filter?: string;
  include_archived?: boolean;
  tls_verify?: boolean;
};

// v0.9.0 — Azure Blob Storage (Tier 2 PR 2). Hostless: account name
// + container + auth fields on the source. Three auth modes:
//   - account_key: Shared Key. Account access key over stdin.
//   - sas_token: Shared Access Signature query string.
//   - azure_ad: DefaultAzureCredential — pod identity / env / az login.
// endpoint_suffix defaults to "core.windows.net"; sovereign clouds
// (US gov / China) can override with "core.usgovcloudapi.net" etc.
export type AzureBlobAuthMode = "account_key" | "sas_token" | "azure_ad";

export type AzureBlobConfig = {
  account_name: string;
  container: string;
  auth_mode: AzureBlobAuthMode;
  account_key?: string;
  sas_token?: string;
  endpoint_suffix?: string;
};

// v0.10.0 — Google Cloud Storage (Tier 2 PR 3). Hostless: bucket +
// auth fields on the source. Two auth modes:
//   - service_account_json: paste service-account JSON key contents.
//   - application_default: ADC chain (workload identity / env / gcloud).
// HMAC users go through the S3 source type with endpoint
// `https://storage.googleapis.com` instead — the existing S3
// connector handles the XML API correctly.
export type GCSAuthMode = "service_account_json" | "application_default";

export type GCSConfig = {
  bucket: string;
  prefix?: string;
  auth_mode: GCSAuthMode;
  service_account_json?: string;
};

// v0.11.0 — WebDAV (Tier 4 PR 1). Hostless: URL + basic auth on the
// source. The URL points at the share root: for Nextcloud,
// https://nextcloud.example.com/remote.php/dav/files/<user>/; for
// generic mod_dav installs, just the share's mount point. Empty
// username/password is allowed for read-only public shares.
export type WebDAVConfig = {
  url: string;
  username?: string;
  password?: string;
  // Default true. Set to false for self-signed home installs; UI
  // surfaces a warning when the toggle is off.
  tls_verify?: boolean;
};

// v0.14.0 — Google Drive (Tier 1 PR-C). The OAuth credential
// (client_id / refresh token / access token) lives on the
// SourceOAuthCredential row; the source's connection_config carries
// only the optional folder scope. ``oauth_credential_id`` is set
// post-Sign-in by the Add Source flow; the absence of it means
// "no Drive account connected yet" and disables save.
export type GDriveConfig = {
  oauth_credential_id?: string;
  // Optional — when empty, walk My Drive root. When set, walk only
  // this folder ID's subtree.
  folder_id?: string;
};

// v0.15.0 — OneDrive (Microsoft Graph). Same OAuth-shaped pattern
// as gdrive: oauth_credential_id is the link to the
// SourceOAuthCredential row created by the Sign-in flow. ``item_id``
// is the optional drive-item id to scope the walk; empty walks the
// drive root.
export type OneDriveConfig = {
  oauth_credential_id?: string;
  item_id?: string;
};

// v0.16.0 — SharePoint document library. OAuth-shaped via Microsoft
// Graph (shares the OneDrive client). ``site_id`` is the colon-
// triple Graph uses to address a site; required.
// ``drive_id`` is optional — empty falls through to the site's
// default document library. ``item_id`` is the optional starting
// folder.
export type SharePointConfig = {
  oauth_credential_id?: string;
  site_id?: string;
  drive_id?: string;
  item_id?: string;
};

// v0.17.0 — Dropbox. OAuth-shaped (Tier 4 PR 2). Path-based
// addressing: ``path`` is optional (empty == scan from the root of
// the user's Dropbox); non-empty must start with ``/``.
export type DropboxConfig = {
  oauth_credential_id?: string;
  path?: string;
};

// v0.18.0 — Box. OAuth-shaped (Tier 4 PR 3). Opaque-id addressing
// like Drive — ``folder_id`` is optional (empty maps to the literal
// "0", Box's All Files root).
//
// v0.19.0 added JWT app-auth as a second variant. When ``auth_mode
// == "jwt"`` the form collects an RSA private key + Box client
// credentials directly rather than going through the Sign-in popup;
// the API mints a JWT assertion at scan/lease time and exchanges it
// for an access token. Default (``auth_mode`` unset or "oauth") keeps
// the v0.18.0 OAuth flow.
export type BoxAuthMode = "oauth" | "jwt";

export type BoxConfig = {
  auth_mode?: BoxAuthMode;
  // OAuth path (v0.18.0).
  oauth_credential_id?: string;
  // JWT app-auth path (v0.19.0). All required when auth_mode="jwt".
  client_id?: string;
  client_secret?: string;
  enterprise_id?: string;
  public_key_id?: string;
  private_key?: string;
  // Optional — only when the PEM is encrypted.
  private_key_passphrase?: string;
  // Common.
  folder_id?: string;
};

export type AnyConfig =
  | LocalConfig
  | NfsConfig
  | SshConfig
  | SmbConfig
  | S3Config
  | PaperlessConfig
  | ImmichConfig
  | AzureBlobConfig
  | GCSConfig
  | WebDAVConfig
  | GDriveConfig
  | OneDriveConfig
  | SharePointConfig
  | DropboxConfig
  | BoxConfig;

export interface FieldsProps<C> {
  value: Partial<C>;
  onChange: (next: Partial<C>) => void;
  // v0.21.0 — per-field errors keyed by field name (matching the
  // mutation key used in onChange). Optional so existing callers that
  // don't pass it stay valid; rendering branches off `error?.[fieldName]`.
  errors?: Record<string, string>;
  onFieldBlur?: (field: string) => void;
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
    case "paperless": {
      if (!isStr("url")) return "URL is required";
      const url = (c["url"] as string).trim();
      if (!/^https?:\/\//i.test(url)) {
        return "URL must start with http:// or https://";
      }
      // Mask handling — "***" sentinel from the api represents
      // "value preserved on disk", same as kerberos password handling
      // in NFS. A field showing "***" is *valid*; only an empty/
      // whitespace value should fail validation.
      if (!isStr("api_token")) return "API token is required";
      return null;
    }
    case "immich": {
      if (!isStr("url")) return "URL is required";
      const url = (c["url"] as string).trim();
      if (!/^https?:\/\//i.test(url)) {
        return "URL must start with http:// or https://";
      }
      if (!isStr("api_key")) return "API key is required";
      return null;
    }
    case "azureblob": {
      if (!isStr("account_name")) return "Account name is required";
      if (!isStr("container")) return "Container is required";
      const mode = (c["auth_mode"] as AzureBlobAuthMode | undefined) ?? "account_key";
      if (mode === "account_key" && !isStr("account_key")) {
        return "Account key is required";
      }
      if (mode === "sas_token" && !isStr("sas_token")) {
        return "SAS token is required";
      }
      // azure_ad has no inline secret — DefaultAzureCredential pulls
      // env / pod identity / az login at scan time.
      return null;
    }
    case "gcs": {
      if (!isStr("bucket")) return "Bucket is required";
      const mode = (c["auth_mode"] as GCSAuthMode | undefined) ?? "service_account_json";
      if (mode === "service_account_json" && !isStr("service_account_json")) {
        return "Service account JSON is required";
      }
      // application_default has no inline secret — ADC chain pulls
      // workload identity / env / gcloud at scan time.
      return null;
    }
    case "webdav": {
      if (!isStr("url")) return "URL is required";
      const url = (c["url"] as string).trim();
      if (!/^https?:\/\//i.test(url)) {
        return "URL must start with http:// or https://";
      }
      // username/password are intentionally optional — read-only
      // public WebDAV shares (rare but legal) need no auth.
      return null;
    }
    case "gdrive": {
      // Sign-in must complete before save. The form sets
      // oauth_credential_id once the popup posts the success
      // payload back. folder_id is optional.
      if (!isStr("oauth_credential_id")) {
        return "Sign in with Google to connect a Drive account";
      }
      return null;
    }
    case "onedrive": {
      if (!isStr("oauth_credential_id")) {
        return "Sign in with Microsoft to connect a OneDrive account";
      }
      return null;
    }
    case "sharepoint": {
      if (!isStr("oauth_credential_id")) {
        return "Sign in with Microsoft to connect a SharePoint account";
      }
      if (!isStr("site_id")) return "Site ID is required";
      return null;
    }
    case "dropbox": {
      if (!isStr("oauth_credential_id")) {
        return "Sign in with Dropbox to connect an account";
      }
      // path is optional; if set, must start with /
      const p = (c["path"] as string | undefined)?.trim();
      if (p && p !== "/" && !p.startsWith("/")) {
        return "Path must start with /";
      }
      return null;
    }
    case "box": {
      const mode = (c["auth_mode"] as string | undefined) ?? "oauth";
      if (mode === "jwt") {
        // JWT app-auth requires the full Box-app config inline.
        for (const k of [
          "client_id", "client_secret", "enterprise_id",
          "public_key_id", "private_key",
        ]) {
          if (!isStr(k)) return `${k.replace(/_/g, " ")} is required`;
        }
        return null;
      }
      // OAuth (default).
      if (!isStr("oauth_credential_id")) {
        return "Sign in with Box to connect an account";
      }
      return null;
    }
  }
}
