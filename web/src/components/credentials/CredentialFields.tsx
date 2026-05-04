import { Input, Select } from "../ui";

export type CredentialType = "ssh" | "smb" | "nfs" | "s3";

export type CredentialValue = Record<string, unknown>;

interface Props {
  type: CredentialType;
  value: CredentialValue;
  onChange: (next: CredentialValue) => void;
}

/**
 * Credential-only subset of the host fields. Used in
 * SettingsCredentials' profile editor and in the inline override
 * disclosure on host / source forms. Distinct from HostFields, which
 * also collects host-shaped keys (host/port/known_hosts_path/etc.)
 * that aren't credentials and shouldn't live on a profile.
 *
 * Each branch mirrors the credential subset of the equivalent HostFields
 * branch so the UI feels consistent: same labels, same placeholders,
 * same "***" sentinel handling for masked secrets.
 */
export function CredentialFields({ type, value, onChange }: Props) {
  switch (type) {
    case "ssh":
      return <SshCredentials value={value} onChange={onChange} />;
    case "smb":
      return <SmbCredentials value={value} onChange={onChange} />;
    case "nfs":
      return <NfsCredentials value={value} onChange={onChange} />;
    case "s3":
      return <S3Credentials value={value} onChange={onChange} />;
  }
}

function val(value: CredentialValue, key: string): string {
  const v = value[key];
  return typeof v === "string" ? v : "";
}

function SshCredentials({ value, onChange }: { value: CredentialValue; onChange: (n: CredentialValue) => void }) {
  const auth = (value.auth as "password" | "key" | undefined) ?? "password";
  const password = val(value, "password");
  const keyPassphrase = val(value, "key_passphrase");
  return (
    <div className="space-y-3">
      <Input
        label="Username"
        value={val(value, "username")}
        onChange={(e) => onChange({ ...value, username: e.target.value })}
      />
      <Select
        label="Authentication"
        value={auth}
        onChange={(e) => onChange({ ...value, auth: e.target.value })}
        options={[
          { value: "password", label: "Password" },
          { value: "key", label: "Private key" },
        ]}
      />
      {auth === "password" ? (
        <Input
          label="Password"
          type="password"
          value={password === "***" ? "" : password}
          onChange={(e) => onChange({ ...value, password: e.target.value })}
          placeholder={password === "***" ? "(unchanged — type to replace)" : ""}
          hint={password === "***" ? "Existing value preserved. Type a new value to replace it." : undefined}
        />
      ) : (
        <>
          <Input
            label="Private key path"
            value={val(value, "key_path")}
            onChange={(e) => onChange({ ...value, key_path: e.target.value })}
            placeholder="/etc/akashic/keys/id_rsa"
          />
          <Input
            label="Key passphrase (optional)"
            type="password"
            value={keyPassphrase === "***" ? "" : keyPassphrase}
            onChange={(e) => onChange({ ...value, key_passphrase: e.target.value })}
            placeholder={keyPassphrase === "***" ? "(unchanged)" : ""}
          />
        </>
      )}
    </div>
  );
}

function SmbCredentials({ value, onChange }: { value: CredentialValue; onChange: (n: CredentialValue) => void }) {
  const password = val(value, "password");
  return (
    <div className="space-y-3">
      <Input
        label="Username"
        value={val(value, "username")}
        onChange={(e) => onChange({ ...value, username: e.target.value })}
      />
      <Input
        label="Password"
        type="password"
        value={password === "***" ? "" : password}
        onChange={(e) => onChange({ ...value, password: e.target.value })}
        placeholder={password === "***" ? "(unchanged — type to replace)" : ""}
        hint={password === "***" ? "Existing value preserved. Type a new value to replace it." : undefined}
      />
      <Input
        label="Domain (optional)"
        value={val(value, "domain")}
        onChange={(e) => onChange({ ...value, domain: e.target.value })}
        placeholder="EXAMPLE"
      />
    </div>
  );
}

function NfsCredentials({ value, onChange }: { value: CredentialValue; onChange: (n: CredentialValue) => void }) {
  // NFS profiles often carry just a Kerberos principal or auth uid/gid;
  // the rest is host-shaped (host/port/export_path) and stays inline.
  return (
    <div className="space-y-3">
      <Input
        label="Auth principal (optional)"
        value={val(value, "krb5_principal")}
        onChange={(e) => onChange({ ...value, krb5_principal: e.target.value })}
        placeholder="user@EXAMPLE.COM"
        hint="Leave blank for AUTH_SYS."
      />
      <Input
        label="Auth UID (optional)"
        value={val(value, "auth_uid")}
        onChange={(e) => onChange({ ...value, auth_uid: e.target.value })}
        placeholder="1000"
      />
      <Input
        label="Auth GID (optional)"
        value={val(value, "auth_gid")}
        onChange={(e) => onChange({ ...value, auth_gid: e.target.value })}
        placeholder="1000"
      />
    </div>
  );
}

function S3Credentials({ value, onChange }: { value: CredentialValue; onChange: (n: CredentialValue) => void }) {
  const secret = val(value, "secret_access_key");
  return (
    <div className="space-y-3">
      <Input
        label="Access key ID"
        value={val(value, "access_key_id")}
        onChange={(e) => onChange({ ...value, access_key_id: e.target.value })}
      />
      <Input
        label="Secret access key"
        type="password"
        value={secret === "***" ? "" : secret}
        onChange={(e) => onChange({ ...value, secret_access_key: e.target.value })}
        placeholder={secret === "***" ? "(unchanged — type to replace)" : ""}
        hint={secret === "***" ? "Existing value preserved. Type a new value to replace it." : undefined}
      />
    </div>
  );
}
