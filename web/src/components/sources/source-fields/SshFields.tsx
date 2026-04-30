import { Input, Select } from "../../ui";
import type { FieldsProps, SshConfig } from "../sourceTypes";

export function SshFields({ value, onChange }: FieldsProps<SshConfig>) {
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
          value={value.password ?? ""}
          onChange={(e) => onChange({ ...value, password: e.target.value })}
          required
        />
      ) : (
        <>
          <Input
            label="Private key path (on the api container)"
            value={value.key_path ?? ""}
            onChange={(e) => onChange({ ...value, key_path: e.target.value })}
            placeholder="/etc/akashic/keys/id_rsa"
            required
          />
          <Input
            label="Key passphrase (optional)"
            type="password"
            value={value.key_passphrase ?? ""}
            onChange={(e) => onChange({ ...value, key_passphrase: e.target.value })}
          />
        </>
      )}
      <Input
        label="Known hosts path"
        value={value.known_hosts_path ?? ""}
        onChange={(e) => onChange({ ...value, known_hosts_path: e.target.value })}
        placeholder="/etc/ssh/known_hosts"
        required
      />
      <p className="text-xs text-fg-muted">
        Strict host-key checking by default — known_hosts must contain the
        target's key fingerprint.
      </p>
    </div>
  );
}
