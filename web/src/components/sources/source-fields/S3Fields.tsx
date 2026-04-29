import { Input } from "../../ui";
import type { FieldsProps, S3Config } from "../sourceTypes";

export function S3Fields({ value, onChange }: FieldsProps<S3Config>) {
  return (
    <div className="space-y-3">
      <Input
        label="Endpoint (optional, for non-AWS / MinIO)"
        value={value.endpoint ?? ""}
        onChange={(e) => onChange({ ...value, endpoint: e.target.value })}
        placeholder="https://s3.us-east-1.amazonaws.com"
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
        placeholder="us-east-1"
        required
      />
      <Input
        label="Access key ID"
        value={value.access_key_id ?? ""}
        onChange={(e) => onChange({ ...value, access_key_id: e.target.value })}
        required
      />
      <Input
        label="Secret access key"
        type="password"
        value={value.secret_access_key ?? ""}
        onChange={(e) => onChange({ ...value, secret_access_key: e.target.value })}
        required
      />
    </div>
  );
}
