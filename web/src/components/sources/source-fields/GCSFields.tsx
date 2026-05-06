/**
 * Google Cloud Storage (v0.10.0) source-config form.
 *
 * Hostless source type. Bucket + (optional) prefix + auth fields all
 * live on the source. Two auth modes:
 *
 *   - service_account_json: paste the contents of a service account
 *     JSON key file (Google Cloud → IAM & Admin → Service Accounts →
 *     Keys → Add key → Create new key → JSON).
 *   - application_default: Application Default Credentials. Picks
 *     up GKE workload identity, env-var-pointed-to JSON
 *     (GOOGLE_APPLICATION_CREDENTIALS), or `gcloud auth
 *     application-default login` creds.
 *
 * HMAC keys aren't a first-class GCS auth mode here — users with
 * only HMAC creds can add an S3 source pointed at
 * `https://storage.googleapis.com` instead. The S3 connector handles
 * the GCS XML API correctly.
 */
import { Input, Select } from "../../ui";
import type { FieldsProps, GCSAuthMode, GCSConfig } from "../sourceTypes";

const AUTH_OPTIONS: { value: GCSAuthMode; label: string; hint: string }[] = [
  {
    value: "service_account_json",
    label: "Service account JSON",
    hint: "Paste the contents of a service account JSON key. Service-to-service auth against a project the akashic operator controls.",
  },
  {
    value: "application_default",
    label: "Application default (workload identity)",
    hint: "ADC chain — GKE workload identity, GOOGLE_APPLICATION_CREDENTIALS env, or `gcloud auth application-default login` at scan time. No inline secret.",
  },
];

export function GCSFields({ value, onChange, errors, onFieldBlur }: FieldsProps<GCSConfig>) {
  const mode: GCSAuthMode = value.auth_mode ?? "service_account_json";
  const optMeta = AUTH_OPTIONS.find((o) => o.value === mode)!;

  return (
    <div className="space-y-3">
      <Input
        label="Bucket"
        value={value.bucket ?? ""}
        onChange={(e) => onChange({ ...value, bucket: e.target.value })}
        placeholder="my-data-bucket"
        required
        error={errors?.bucket}
        onBlur={() => onFieldBlur?.("bucket")}
      />
      <Input
        label="Prefix (optional)"
        value={value.prefix ?? ""}
        onChange={(e) => onChange({ ...value, prefix: e.target.value })}
        placeholder="archive/2024/"
      />
      <p className="text-[11px] text-fg-muted -mt-1">
        Object key prefix. Leave blank to index the whole bucket. Use
        when you only want a subtree indexed (e.g., a per-tenant
        prefix in a shared bucket).
      </p>

      <Select
        label="Auth mode"
        value={mode}
        onChange={(e) => onChange({ ...value, auth_mode: e.target.value as GCSAuthMode })}
        options={AUTH_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
      />
      <p className="text-[11px] text-fg-muted -mt-1">{optMeta.hint}</p>

      {mode === "service_account_json" && (
        <div>
          <label className="block text-xs font-medium text-fg mb-1">
            Service account JSON key
          </label>
          {/* Multi-line because the JSON is ~2KB; the password Input
              type would clip to a single line. The actual masking is
              done at the api on response (key name contains "json"
              → masked as "***" by _scrub_config). */}
          <textarea
            value={value.service_account_json === "***" ? "" : (value.service_account_json ?? "")}
            onChange={(e) => onChange({ ...value, service_account_json: e.target.value })}
            placeholder={
              value.service_account_json === "***"
                ? "(unchanged — paste a new JSON key to replace)"
                : '{\n  "type": "service_account",\n  "project_id": "…",\n  "private_key": "…",\n  …\n}'
            }
            rows={8}
            className="w-full rounded-md border border-line px-3 py-2 text-xs font-mono bg-surface focus:outline-none focus:ring-2 focus:ring-accent-400 focus:border-accent-400"
            spellCheck={false}
            required={value.service_account_json !== "***"}
          />
          <p className="text-[11px] text-fg-muted mt-1">
            Created in Google Cloud → <em>IAM &amp; Admin → Service
            Accounts → Keys → Add key</em>. The account needs the{" "}
            <em>Storage Object Viewer</em> role on this bucket.
          </p>
        </div>
      )}

      {mode === "application_default" && (
        <div className="rounded-md border border-line bg-app px-3 py-2 text-xs text-fg-muted">
          The scanner will request a token via Google's ADC chain:
          GKE workload identity → <code>GOOGLE_APPLICATION_CREDENTIALS</code>{" "}
          env var → <code>gcloud auth application-default login</code>{" "}
          creds. For workload identity in GKE, the scanner pod's
          Kubernetes service account must be bound to a Google
          service account that has{" "}
          <em>Storage Object Viewer</em> on this bucket.
        </div>
      )}
    </div>
  );
}
