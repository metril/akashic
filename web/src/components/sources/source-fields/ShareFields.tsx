import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { Input } from "../../ui";
import type {
  AzureBlobAuthMode,
  AzureBlobConfig,
  BoxConfig,
  DropboxConfig,
  GCSAuthMode,
  GCSConfig,
  GDriveConfig,
  ImmichConfig,
  LocalConfig,
  NfsConfig,
  OneDriveConfig,
  PaperlessConfig,
  S3Config,
  SharePointConfig,
  SmbConfig,
  SourceType,
  SshConfig,
  WebDAVConfig,
} from "../sourceTypes";
import { PaperlessFields } from "./PaperlessFields";
import { ImmichFields } from "./ImmichFields";
import { AzureBlobFields } from "./AzureBlobFields";
import { GCSFields } from "./GCSFields";
import { WebDAVFields } from "./WebDAVFields";
import { GDriveFields } from "./GDriveFields";
import { OneDriveFields } from "./OneDriveFields";
import { SharePointFields } from "./SharePointFields";
import { DropboxFields } from "./DropboxFields";
import { BoxFields } from "./BoxFields";

export type ShareConfig = Partial<
  | LocalConfig
  | SshConfig
  | SmbConfig
  | NfsConfig
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
  | BoxConfig
>;

interface Props {
  type: SourceType;
  value: ShareConfig;
  onChange: (next: ShareConfig) => void;
  errors?: Record<string, string>;
  onFieldBlur?: (field: string) => void;
}

/**
 * Share-only fields per type. Pairs with HostFields.tsx — together
 * they reproduce the legacy SourceFieldSet but split host- vs.
 * share-shaped state so the same widgets work for sources that share
 * a Host row.
 */
export function ShareFields({ type, value, onChange, errors, onFieldBlur }: Props): ReactNode {
  // v0.21.0 — small helpers so per-input error/onBlur threading stays
  // terse in the heavy switch below. `bind("api_token")` produces
  // ``error={errors?.api_token}, onBlur={() => onFieldBlur?.("api_token")}``.
  const bind = (field: string) => ({
    error: errors?.[field],
    onBlur: () => onFieldBlur?.(field),
  });
  switch (type) {
    case "local":
      return (
        <Input
          label="Path"
          value={(value as Partial<LocalConfig>).path ?? ""}
          onChange={(e) => onChange({ ...value, path: e.target.value })}
          placeholder="/home/user/documents"
          required
          {...bind("path")}
        />
      );
    case "ssh": {
      // root_path is a v0.5.0 share-shaped extension to SshConfig that
      // doesn't exist in the type yet — cast through a local extension.
      type SshShare = Partial<SshConfig> & { root_path?: string };
      const v = value as SshShare;
      return (
        <Input
          label="Root path on remote (optional)"
          value={v.root_path ?? ""}
          onChange={(e) =>
            (onChange as (next: SshShare) => void)({
              ...v,
              root_path: e.target.value,
            })
          }
          placeholder="/ (entire remote tree)"
        />
      );
    }
    case "smb":
      return (
        <Input
          label="Share"
          value={(value as Partial<SmbConfig>).share ?? ""}
          onChange={(e) => onChange({ ...value, share: e.target.value })}
          placeholder="public"
          required
          {...bind("share")}
        />
      );
    case "nfs":
      return (
        <NfsShareFields
          value={value as Partial<NfsConfig>}
          onChange={onChange}
          errors={errors}
          onFieldBlur={onFieldBlur}
        />
      );
    case "s3":
      return (
        <S3ShareFields
          value={value as Partial<S3Config>}
          onChange={onChange}
          errors={errors}
          onFieldBlur={onFieldBlur}
        />
      );
    case "paperless":
      // v0.7.0 — hostless. The "share" carries the whole config
      // (url, api_token, optional tag_filter + tls_verify).
      return (
        <PaperlessFields
          value={value as Partial<PaperlessConfig>}
          onChange={onChange as (next: Partial<PaperlessConfig>) => void}
          errors={errors}
          onFieldBlur={onFieldBlur}
        />
      );
    case "immich":
      // v0.8.0 — same hostless shape: url, api_key, optional
      // album_filter + include_archived + tls_verify all on the
      // "share".
      return (
        <ImmichFields
          value={value as Partial<ImmichConfig>}
          onChange={onChange as (next: Partial<ImmichConfig>) => void}
          errors={errors}
          onFieldBlur={onFieldBlur}
        />
      );
    case "azureblob":
      // v0.9.0 — hostless: account_name + container + auth fields
      // all on the "share".
      return (
        <AzureBlobFields
          value={value as Partial<AzureBlobConfig>}
          onChange={onChange as (next: Partial<AzureBlobConfig>) => void}
          errors={errors}
          onFieldBlur={onFieldBlur}
        />
      );
    case "gcs":
      // v0.10.0 — hostless: bucket + (optional) prefix + auth fields
      // all on the "share".
      return (
        <GCSFields
          value={value as Partial<GCSConfig>}
          onChange={onChange as (next: Partial<GCSConfig>) => void}
          errors={errors}
          onFieldBlur={onFieldBlur}
        />
      );
    case "webdav":
      // v0.11.0 — hostless: URL + basic auth + tls_verify all on
      // the "share".
      return (
        <WebDAVFields
          value={value as Partial<WebDAVConfig>}
          onChange={onChange as (next: Partial<WebDAVConfig>) => void}
          errors={errors}
          onFieldBlur={onFieldBlur}
        />
      );
    case "gdrive":
      // v0.14.0 — hostless OAuth-shaped: oauth_credential_id +
      // optional folder_id all on the "share".
      return (
        <GDriveFields
          value={value as Partial<GDriveConfig>}
          onChange={onChange as (next: Partial<GDriveConfig>) => void}
        />
      );
    case "onedrive":
      // v0.15.0 — hostless OAuth-shaped via Microsoft Graph.
      return (
        <OneDriveFields
          value={value as Partial<OneDriveConfig>}
          onChange={onChange as (next: Partial<OneDriveConfig>) => void}
        />
      );
    case "sharepoint":
      // v0.16.0 — hostless OAuth-shaped via Microsoft Graph; adds
      // site_id (required) + optional drive_id/item_id.
      return (
        <SharePointFields
          value={value as Partial<SharePointConfig>}
          onChange={onChange as (next: Partial<SharePointConfig>) => void}
        />
      );
    case "dropbox":
      // v0.17.0 — hostless OAuth-shaped via Dropbox.
      return (
        <DropboxFields
          value={value as Partial<DropboxConfig>}
          onChange={onChange as (next: Partial<DropboxConfig>) => void}
        />
      );
    case "box":
      // v0.18.0 — hostless OAuth-shaped via Box.
      return (
        <BoxFields
          value={value as Partial<BoxConfig>}
          onChange={onChange as (next: Partial<BoxConfig>) => void}
        />
      );
  }
}

function NfsShareFields({
  value,
  onChange,
  errors,
  onFieldBlur,
}: {
  value: Partial<NfsConfig>;
  onChange: (next: Partial<NfsConfig>) => void;
  errors?: Record<string, string>;
  onFieldBlur?: (field: string) => void;
}) {
  // Aux GIDs are technically a host-level identity but historically
  // lived on the source. Keep them on the source for backward compat;
  // moving them to the Host is a follow-up.
  const externalAuxString = (value.auth_aux_gids ?? []).join(", ");
  const [auxText, setAuxText] = useState(externalAuxString);
  const lastSyncedExternal = useRef(externalAuxString);
  useEffect(() => {
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
        label="Export path"
        value={value.export_path ?? ""}
        onChange={(e) => onChange({ ...value, export_path: e.target.value })}
        placeholder="/srv/nfs/data"
        required
        error={errors?.export_path}
        onBlur={() => onFieldBlur?.("export_path")}
      />
      <Input
        label="Mount options (optional)"
        value={value.mount_options ?? ""}
        onChange={(e) => onChange({ ...value, mount_options: e.target.value })}
        placeholder="vers=4.1,sec=sys"
      />
      <Input
        label="Aux GIDs (optional, comma-separated)"
        value={auxText}
        onChange={(e) => setAuxText(e.target.value)}
        onBlur={() => commitAuxText(auxText)}
        placeholder="27, 100"
      />
      <Input
        label="Probe timeout (seconds, optional)"
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
        }}
        placeholder="5"
      />
    </div>
  );
}

function S3ShareFields({
  value,
  onChange,
  errors,
  onFieldBlur,
}: {
  value: Partial<S3Config>;
  onChange: (next: Partial<S3Config>) => void;
  errors?: Record<string, string>;
  onFieldBlur?: (field: string) => void;
}) {
  return (
    <Input
      label="Bucket"
      value={value.bucket ?? ""}
      onChange={(e) => onChange({ ...value, bucket: e.target.value })}
      required
      error={errors?.bucket}
      onBlur={() => onFieldBlur?.("bucket")}
    />
  );
}

/**
 * Returns null if `cfg` covers the share-only fields for the given
 * type, or a human-readable reason otherwise. Pairs with
 * validateHostConfig.
 */
export function validateShareConfig(
  type: SourceType,
  cfg: ShareConfig,
): string | null {
  const c = cfg as Record<string, unknown>;
  const isStr = (k: string) =>
    typeof c[k] === "string" && (c[k] as string).trim() !== "";
  switch (type) {
    case "local":
      return isStr("path") ? null : "Path is required";
    case "ssh":
      return null;  // root_path is optional
    case "smb":
      return isStr("share") ? null : "Share is required";
    case "nfs":
      return isStr("export_path") ? null : "Export path is required";
    case "s3":
      return isStr("bucket") ? null : "Bucket is required";
    case "paperless": {
      if (!isStr("url")) return "URL is required";
      const url = (c["url"] as string).trim();
      if (!/^https?:\/\//i.test(url)) {
        return "URL must start with http:// or https://";
      }
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
      return null;
    }
    case "gcs": {
      if (!isStr("bucket")) return "Bucket is required";
      const mode = (c["auth_mode"] as GCSAuthMode | undefined) ?? "service_account_json";
      if (mode === "service_account_json" && !isStr("service_account_json")) {
        return "Service account JSON is required";
      }
      return null;
    }
    case "webdav": {
      if (!isStr("url")) return "URL is required";
      const url = (c["url"] as string).trim();
      if (!/^https?:\/\//i.test(url)) {
        return "URL must start with http:// or https://";
      }
      return null;
    }
    case "gdrive": {
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
      const p = (c["path"] as string | undefined)?.trim();
      if (p && p !== "/" && !p.startsWith("/")) {
        return "Path must start with /";
      }
      return null;
    }
    case "box": {
      const mode = (c["auth_mode"] as string | undefined) ?? "oauth";
      if (mode === "jwt") {
        for (const k of [
          "client_id", "client_secret", "enterprise_id",
          "public_key_id", "private_key",
        ]) {
          if (!isStr(k)) return `${k.replace(/_/g, " ")} is required`;
        }
        return null;
      }
      if (!isStr("oauth_credential_id")) {
        return "Sign in with Box to connect an account";
      }
      return null;
    }
  }
}

/**
 * v0.21.0 — per-field error map. Returns the same set of validations
 * as ``validateShareConfig`` but keyed by field name so per-input red
 * borders work. The form-level validator (AddSourceForm) keeps using
 * the single-string getter; this is purely additive.
 *
 * Fields not present in the map are valid. The mapping intentionally
 * mirrors the field NAMES used in each FieldsProps onChange handler so
 * subcomponents can look up errors by the same key they write.
 */
export function validateShareConfigFields(
  type: SourceType,
  cfg: ShareConfig,
): Record<string, string> {
  const c = cfg as Record<string, unknown>;
  const isStr = (k: string) =>
    typeof c[k] === "string" && (c[k] as string).trim() !== "";
  const out: Record<string, string> = {};
  switch (type) {
    case "local":
      if (!isStr("path")) out.path = "Path is required";
      return out;
    case "ssh":
      return out;
    case "smb":
      if (!isStr("share")) out.share = "Share is required";
      return out;
    case "nfs":
      if (!isStr("export_path")) out.export_path = "Export path is required";
      return out;
    case "s3":
      if (!isStr("bucket")) out.bucket = "Bucket is required";
      return out;
    case "paperless": {
      if (!isStr("url")) out.url = "URL is required";
      else if (!/^https?:\/\//i.test((c.url as string).trim())) {
        out.url = "URL must start with http:// or https://";
      }
      if (!isStr("api_token")) out.api_token = "API token is required";
      return out;
    }
    case "immich": {
      if (!isStr("url")) out.url = "URL is required";
      else if (!/^https?:\/\//i.test((c.url as string).trim())) {
        out.url = "URL must start with http:// or https://";
      }
      if (!isStr("api_key")) out.api_key = "API key is required";
      return out;
    }
    case "azureblob": {
      if (!isStr("account_name")) out.account_name = "Account name is required";
      if (!isStr("container")) out.container = "Container is required";
      const mode = (c.auth_mode as AzureBlobAuthMode | undefined) ?? "account_key";
      if (mode === "account_key" && !isStr("account_key")) {
        out.account_key = "Account key is required";
      }
      if (mode === "sas_token" && !isStr("sas_token")) {
        out.sas_token = "SAS token is required";
      }
      return out;
    }
    case "gcs": {
      if (!isStr("bucket")) out.bucket = "Bucket is required";
      const mode = (c.auth_mode as GCSAuthMode | undefined) ?? "service_account_json";
      if (mode === "service_account_json" && !isStr("service_account_json")) {
        out.service_account_json = "Service account JSON is required";
      }
      return out;
    }
    case "webdav": {
      if (!isStr("url")) out.url = "URL is required";
      else if (!/^https?:\/\//i.test((c.url as string).trim())) {
        out.url = "URL must start with http:// or https://";
      }
      return out;
    }
    case "gdrive":
      if (!isStr("oauth_credential_id"))
        out.oauth_credential_id = "Sign in with Google to connect a Drive account";
      return out;
    case "onedrive":
      if (!isStr("oauth_credential_id"))
        out.oauth_credential_id = "Sign in with Microsoft to connect a OneDrive account";
      return out;
    case "sharepoint":
      if (!isStr("oauth_credential_id"))
        out.oauth_credential_id = "Sign in with Microsoft to connect a SharePoint account";
      if (!isStr("site_id")) out.site_id = "Site ID is required";
      return out;
    case "dropbox": {
      if (!isStr("oauth_credential_id"))
        out.oauth_credential_id = "Sign in with Dropbox to connect an account";
      const p = (c.path as string | undefined)?.trim();
      if (p && p !== "/" && !p.startsWith("/")) out.path = "Path must start with /";
      return out;
    }
    case "box": {
      const mode = (c.auth_mode as string | undefined) ?? "oauth";
      if (mode === "jwt") {
        for (const k of [
          "client_id", "client_secret", "enterprise_id",
          "public_key_id", "private_key",
        ]) {
          if (!isStr(k)) out[k] = `${k.replace(/_/g, " ")} is required`;
        }
        return out;
      }
      if (!isStr("oauth_credential_id"))
        out.oauth_credential_id = "Sign in with Box to connect an account";
      return out;
    }
  }
}
