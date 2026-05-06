/**
 * Azure Blob Storage (v0.9.0) source-config form.
 *
 * Hostless source type. Account name + container + auth fields all
 * live on the source. Three auth modes:
 *
 *   - account_key: paste the Shared Key from Azure portal → Storage
 *     account → Access keys. Easiest one-off but rotates poorly.
 *   - sas_token: paste a Shared Access Signature query string. Bounded
 *     lifetime + scoped permissions; cleanest fit when akashic's
 *     operator and the Azure tenant don't share an identity.
 *   - azure_ad: DefaultAzureCredential. Picks up workload identity
 *     (AKS), managed identity, env vars, or `az login` creds at scan
 *     time. The recommended production path — no secret to rotate.
 *
 * The form swaps the credential input based on the chosen auth mode
 * so the user only ever sees the field that matters.
 */
import { Input, Select } from "../../ui";
import type {
  AzureBlobAuthMode,
  AzureBlobConfig,
  FieldsProps,
} from "../sourceTypes";

const AUTH_OPTIONS: { value: AzureBlobAuthMode; label: string; hint: string }[] = [
  {
    value: "account_key",
    label: "Account key (Shared Key)",
    hint: "Easiest. Paste the storage account's access key. Rotates poorly — prefer Azure AD for production.",
  },
  {
    value: "sas_token",
    label: "SAS token",
    hint: "Shared Access Signature with bounded lifetime. Paste the query string (with or without the leading '?').",
  },
  {
    value: "azure_ad",
    label: "Azure AD (workload identity)",
    hint: "DefaultAzureCredential — picks up pod identity / managed identity / env / az login at scan time. No inline secret.",
  },
];

export function AzureBlobFields({ value, onChange }: FieldsProps<AzureBlobConfig>) {
  const mode: AzureBlobAuthMode = value.auth_mode ?? "account_key";
  const optMeta = AUTH_OPTIONS.find((o) => o.value === mode)!;

  return (
    <div className="space-y-3">
      <Input
        label="Storage account name"
        value={value.account_name ?? ""}
        onChange={(e) => onChange({ ...value, account_name: e.target.value })}
        placeholder="mystorageaccount"
        required
      />
      <Input
        label="Container"
        value={value.container ?? ""}
        onChange={(e) => onChange({ ...value, container: e.target.value })}
        placeholder="data"
        required
      />

      <Select
        label="Auth mode"
        value={mode}
        onChange={(e) => onChange({ ...value, auth_mode: e.target.value as AzureBlobAuthMode })}
        options={AUTH_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
      />
      <p className="text-[11px] text-fg-muted -mt-1">{optMeta.hint}</p>

      {mode === "account_key" && (
        <Input
          label="Account key"
          type="password"
          value={value.account_key === "***" ? "" : (value.account_key ?? "")}
          onChange={(e) => onChange({ ...value, account_key: e.target.value })}
          placeholder={
            value.account_key === "***" ? "(unchanged — type to replace)" : ""
          }
          autoComplete="new-password"
          required={value.account_key !== "***"}
        />
      )}

      {mode === "sas_token" && (
        <Input
          label="SAS token"
          type="password"
          value={value.sas_token === "***" ? "" : (value.sas_token ?? "")}
          onChange={(e) => onChange({ ...value, sas_token: e.target.value })}
          placeholder={
            value.sas_token === "***"
              ? "(unchanged — type to replace)"
              : "?sv=2022-11-02&sig=…"
          }
          autoComplete="new-password"
          required={value.sas_token !== "***"}
        />
      )}

      {mode === "azure_ad" && (
        <div className="rounded-md border border-line bg-app px-3 py-2 text-xs text-fg-muted">
          The scanner will request a token via{" "}
          <code>DefaultAzureCredential</code>. For pod identity in AKS,
          ensure the scanner pod's service account is bound to a
          managed-identity that has the{" "}
          <em>Storage Blob Data Reader</em> role on this storage
          account.
        </div>
      )}

      <Input
        label="Endpoint suffix (optional)"
        value={value.endpoint_suffix ?? ""}
        onChange={(e) => onChange({ ...value, endpoint_suffix: e.target.value })}
        placeholder="core.windows.net"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Defaults to <code>core.windows.net</code>. Set for sovereign
        clouds: <code>core.usgovcloudapi.net</code> (US gov),{" "}
        <code>core.chinacloudapi.cn</code> (China),{" "}
        <code>core.cloudapi.de</code> (legacy Germany).
      </p>
    </div>
  );
}
