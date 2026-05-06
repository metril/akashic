import { useMemo } from "react";
import { Input, MaskedInput, Select } from "../../ui";
import {
  S3_PRESETS,
  type FieldsProps,
  type S3Config,
  type S3Preset,
} from "../sourceTypes";

const PRESET_OPTIONS = (Object.keys(S3_PRESETS) as S3Preset[]).map((k) => ({
  value: k,
  label: S3_PRESETS[k].label,
}));

// v0.8.1 — derive which preset the current config matches so the
// dropdown sticks on edit. Endpoint (case-insensitive substring) is
// the strongest signal; path_style breaks ties for AWS. Defaults to
// "other" so a custom endpoint doesn't accidentally show "AWS".
function detectPreset(cfg: Partial<S3Config>): S3Preset {
  const endpoint = (cfg.endpoint ?? "").toLowerCase();
  if (!endpoint) return "aws";
  if (endpoint.includes("amazonaws.com")) return "aws";
  if (endpoint.includes("wasabisys.com")) return "wasabi";
  if (endpoint.includes("backblazeb2.com")) return "backblaze_b2";
  // MinIO is the only preset that forces path_style=true; if the
  // user kept that and the endpoint looks non-AWS, surface MinIO.
  if (cfg.path_style === true) return "minio";
  return "other";
}

export function S3Fields({ value, onChange }: FieldsProps<S3Config>) {
  const preset: S3Preset = useMemo(() => detectPreset(value), [value]);
  const meta = S3_PRESETS[preset];

  function applyPreset(next: S3Preset) {
    const m = S3_PRESETS[next];
    // Only overwrite endpoint when switching FROM a preset the user
    // hadn't customised — i.e., the previous endpoint was empty or
    // matched the previous preset's placeholder. Otherwise keep
    // their typed value.
    const endpoint = m.endpoint ?? value.endpoint ?? "";
    onChange({
      ...value,
      endpoint,
      path_style: m.path_style,
    });
    void next;
  }

  return (
    <div className="space-y-3">
      <Select
        label="Provider preset"
        value={preset}
        onChange={(e) => applyPreset(e.target.value as S3Preset)}
        options={PRESET_OPTIONS}
      />
      <p className="text-[11px] text-fg-muted -mt-1">{meta.hint}</p>

      <Input
        label={preset === "aws" ? "Endpoint (optional, AWS uses default)" : "Endpoint"}
        value={value.endpoint ?? ""}
        onChange={(e) => onChange({ ...value, endpoint: e.target.value })}
        placeholder={meta.endpoint_placeholder ?? "https://s3.us-east-1.amazonaws.com"}
      />
      <Input
        label="Bucket"
        value={value.bucket ?? ""}
        onChange={(e) => onChange({ ...value, bucket: e.target.value })}
        required
      />
      <Input
        label="Region"
        value={value.region ?? ""}
        onChange={(e) => onChange({ ...value, region: e.target.value })}
        placeholder={meta.region_placeholder}
        required
      />
      <Input
        label="Access key ID"
        value={value.access_key_id ?? ""}
        onChange={(e) => onChange({ ...value, access_key_id: e.target.value })}
        required
      />
      <MaskedInput
        label="Secret access key"
        value={value.secret_access_key === "***" ? "" : (value.secret_access_key ?? "")}
        onChange={(e) => onChange({ ...value, secret_access_key: e.target.value })}
        placeholder={value.secret_access_key === "***" ? "(unchanged — type to replace)" : ""}
        required={value.secret_access_key !== "***"}
      />
      {/* Power-user override. Hidden by default; visible only when
          the user has explicitly set it (i.e., they came from the
          host edit drawer with an existing override). Most users
          should rely on the preset's default. */}
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
              ? "auto — path-style when endpoint is set"
              : meta.path_style ? "on" : "off"}
            )
          </span>
        </label>
      )}
    </div>
  );
}
