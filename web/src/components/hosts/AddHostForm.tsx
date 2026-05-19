import { useEffect, useState } from "react";
import { Button, Card, CardHeader, Input, Select } from "../ui";
import { useCreateHost } from "../../hooks/useHosts";
import {
  HostFields,
  type HostConfig,
  type HostType,
  validateHostConfig,
} from "../sources/source-fields/HostFields";
import { InheritableNumberField } from "../sources/source-fields/InheritableNumberField";
import { ProfilePicker } from "../credentials/ProfilePicker";

const HOST_TYPE_OPTIONS: { value: HostType; label: string }[] = [
  { value: "smb", label: "SMB / CIFS" },
  { value: "nfs", label: "NFS" },
  { value: "s3", label: "S3-compatible" },
];

interface Props {
  onCreated?: () => void;
}

export function AddHostForm({ onCreated }: Props) {
  const createHost = useCreateHost();

  const [name, setName] = useState("");
  const [type, setType] = useState<HostType>("smb");
  const [config, setConfig] = useState<HostConfig>({});
  const [profileId, setProfileId] = useState<string | null>(null);
  // Scan-distribution defaults inherited by every attached source.
  // null = no host-level setting (built-in default applies). v0.35.0.
  const [maxParallelScanners, setMaxParallelScanners] = useState<number | null>(null);
  const [scanChunkSize, setScanChunkSize] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    setConfig({} as HostConfig);
    setProfileId(null);
    setFormError(null);
  }, [type]);

  const validationError = validateHostConfig(type, config, profileId !== null);
  const canSubmit = name.trim() !== "" && validationError === null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!canSubmit) {
      setFormError(validationError ?? "Name is required");
      return;
    }
    try {
      await createHost.mutateAsync({
        name,
        type,
        connection_config: config as Record<string, unknown>,
        credential_profile_id: profileId,
        max_parallel_scanners: maxParallelScanners,
        scan_chunk_size: scanChunkSize,
      });
      setName("");
      setConfig({} as HostConfig);
      setProfileId(null);
      setMaxParallelScanners(null);
      setScanChunkSize(null);
      onCreated?.();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to create host",
      );
    }
  }

  return (
    <Card padding="md">
      <CardHeader
        title="Add a host"
        description="A reusable connection target. Add many shares to one host without re-entering credentials."
      />
      <form onSubmit={handleSubmit} className="space-y-3">
        <Input
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="fileserver01"
          required
        />
        <Select
          label="Type"
          value={type}
          onChange={(e) => setType(e.target.value as HostType)}
          options={HOST_TYPE_OPTIONS}
        />
        <ProfilePicker
          type={type}
          value={profileId}
          onChange={setProfileId}
          hint={
            profileId
              ? "Credentials come from this profile. Edit them in Settings → Credentials."
              : "Pick a saved profile, or fill credentials inline below."
          }
        />
        <HostFields
          type={type}
          value={config}
          onChange={setConfig}
          omitCredentials={profileId !== null}
        />
        <InheritableNumberField
          label="Max parallel scanners"
          value={maxParallelScanners}
          onChange={setMaxParallelScanners}
          min={1}
          max={16}
          fallback={1}
          inheritLabel="Use the built-in default"
          hint="Default cap (1–16) on cooperating scanners per scan, inherited by every attached share that doesn't set its own. The built-in default is 1."
        />
        <InheritableNumberField
          label="Scan chunk size"
          value={scanChunkSize}
          onChange={setScanChunkSize}
          min={100}
          max={1000000}
          fallback={2000}
          inheritLabel="Use the built-in default"
          hint="Default work-unit entry budget inherited by every attached share that doesn't set its own. The built-in default is 2000."
        />

        {formError && (
          <p className="text-xs text-rose-600" role="alert">
            {formError}
          </p>
        )}

        <Button
          type="submit"
          loading={createHost.isPending}
          disabled={!canSubmit}
          className="w-full"
        >
          Add host
        </Button>
      </form>
    </Card>
  );
}
