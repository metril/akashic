import { useEffect, useState } from "react";
import { Button, Card, CardHeader, Input, Select } from "../ui";
import { useCreateSource } from "../../hooks/useSources";
import { useTestSource, type TestSourceResult } from "../../hooks/useTestSource";
import {
  SOURCE_TYPES,
  SOURCE_TYPE_LABELS,
  validateSourceConfig,
  type AnyConfig,
  type SourceType,
} from "./sourceTypes";
import { LocalFields } from "./source-fields/LocalFields";
import { NfsFields } from "./source-fields/NfsFields";
import { SshFields } from "./source-fields/SshFields";
import { SmbFields } from "./source-fields/SmbFields";
import { S3Fields } from "./source-fields/S3Fields";

const SOURCE_TYPE_OPTIONS = SOURCE_TYPES.map((t) => ({
  value: t,
  label: SOURCE_TYPE_LABELS[t],
}));

interface AddSourceFormProps {
  onCreated?: () => void;
}

export function AddSourceForm({ onCreated }: AddSourceFormProps) {
  const createSource = useCreateSource();
  const testSource = useTestSource();

  const [name, setName] = useState("");
  const [type, setType] = useState<SourceType>("local");
  const [config, setConfig] = useState<Partial<AnyConfig>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestSourceResult | null>(null);

  useEffect(() => {
    setConfig(type === "ssh" ? { auth: "password" } : ({} as Partial<AnyConfig>));
    setTestResult(null);
    setFormError(null);
  }, [type]);

  const validationError = validateSourceConfig(type, config);
  const canSubmit = name.trim() !== "" && validationError === null;

  function renderFields() {
    switch (type) {
      case "local":
        return (
          <LocalFields value={config as never} onChange={setConfig as never} />
        );
      case "nfs":
        return (
          <NfsFields value={config as never} onChange={setConfig as never} />
        );
      case "ssh":
        return (
          <SshFields value={config as never} onChange={setConfig as never} />
        );
      case "smb":
        return (
          <SmbFields value={config as never} onChange={setConfig as never} />
        );
      case "s3":
        return (
          <S3Fields value={config as never} onChange={setConfig as never} />
        );
    }
  }

  async function handleTest() {
    setTestResult(null);
    setFormError(null);
    try {
      const r = await testSource.mutateAsync({
        type,
        connection_config: config as Record<string, unknown>,
      });
      setTestResult(r);
    } catch (err) {
      setTestResult({
        ok: false,
        step: null,
        error: err instanceof Error ? err.message : "Test failed",
      });
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!canSubmit) {
      setFormError(validationError ?? "Name is required");
      return;
    }
    try {
      await createSource.mutateAsync({
        name,
        type,
        connection_config: config as Record<string, unknown>,
      });
      setName("");
      setConfig(type === "ssh" ? { auth: "password" } : ({} as Partial<AnyConfig>));
      setTestResult(null);
      onCreated?.();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to create source",
      );
    }
  }

  return (
    <Card padding="md">
      <CardHeader title="Add a source" description="Index any reachable filesystem." />
      <form onSubmit={handleSubmit} className="space-y-3">
        <Input
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="My Documents"
          required
        />
        <Select
          label="Type"
          value={type}
          onChange={(e) => setType(e.target.value as SourceType)}
          options={SOURCE_TYPE_OPTIONS}
        />
        {renderFields()}

        {testResult && (
          <div
            className={`rounded-md p-2 text-xs ${
              testResult.ok
                ? "bg-emerald-50 text-emerald-800"
                : "bg-rose-50 text-rose-800"
            }`}
            role="status"
          >
            {testResult.ok
              ? "Connection OK"
              : `${testResult.step ?? "error"}: ${testResult.error ?? "unknown"}`}
          </div>
        )}

        {formError && (
          <p className="text-xs text-rose-600" role="alert">
            {formError}
          </p>
        )}

        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={handleTest}
            loading={testSource.isPending}
            disabled={validationError !== null}
            title={validationError ?? undefined}
          >
            Test
          </Button>
          <Button
            type="submit"
            loading={createSource.isPending}
            disabled={!canSubmit}
            className="flex-1"
          >
            Add source
          </Button>
        </div>
      </form>
    </Card>
  );
}
