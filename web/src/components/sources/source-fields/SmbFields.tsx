import { Input } from "../../ui";
import type { FieldsProps, SmbConfig } from "../sourceTypes";

export function SmbFields({ value, onChange }: FieldsProps<SmbConfig>) {
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
      <Input
        label="Share"
        value={value.share ?? ""}
        onChange={(e) => onChange({ ...value, share: e.target.value })}
        placeholder="public"
        required
      />
      <Input
        label="Username"
        value={value.username ?? ""}
        onChange={(e) => onChange({ ...value, username: e.target.value })}
        required
      />
      <Input
        label="Password"
        type="password"
        value={value.password ?? ""}
        onChange={(e) => onChange({ ...value, password: e.target.value })}
        required
      />
      <Input
        label="Domain (optional)"
        value={value.domain ?? ""}
        onChange={(e) => onChange({ ...value, domain: e.target.value })}
        placeholder="EXAMPLE"
      />
    </div>
  );
}
