import { useEffect, useRef, useState } from "react";
import { Button, Card, CardHeader, Input, Select } from "../ui";
import { useCreateSource } from "../../hooks/useSources";
import { useTestSource, type TestSourceResult } from "../../hooks/useTestSource";
import { inferIsRemovable } from "../../lib/sources";
import {
  SOURCE_TYPES,
  SOURCE_TYPE_LABELS,
  validateSourceConfig,
  type AnyConfig,
  type SourceType,
} from "./sourceTypes";
import { SourceFieldSet } from "./SourceFieldSet";

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
  const [preferredPool, setPreferredPool] = useState("");
  const [isRemovable, setIsRemovable] = useState(false);
  // Track whether the user has explicitly toggled the checkbox. Until
  // they do, the checkbox follows the inferred default as they edit
  // type/path. Once they touch it, we stop overriding their choice.
  const removableTouched = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestSourceResult | null>(null);

  useEffect(() => {
    setConfig(type === "ssh" ? { auth: "password" } : ({} as Partial<AnyConfig>));
    setTestResult(null);
    setFormError(null);
    if (!removableTouched.current) {
      setIsRemovable(inferIsRemovable(type, {}));
    }
  }, [type]);

  // Re-infer when the user types into the local-source path field.
  // Only runs while the user hasn't explicitly toggled the checkbox.
  useEffect(() => {
    if (removableTouched.current) return;
    setIsRemovable(inferIsRemovable(type, config as Record<string, unknown>));
  }, [type, config]);

  const validationError = validateSourceConfig(type, config);
  const canSubmit = name.trim() !== "" && validationError === null;

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
        preferred_pool: preferredPool.trim() || null,
        is_removable: isRemovable,
      });
      setName("");
      setConfig(type === "ssh" ? { auth: "password" } : ({} as Partial<AnyConfig>));
      setPreferredPool("");
      setIsRemovable(false);
      removableTouched.current = false;
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
        <SourceFieldSet type={type} value={config} onChange={setConfig} />
        <Input
          label="Preferred scanner pool"
          value={preferredPool}
          onChange={(e) => setPreferredPool(e.target.value)}
          placeholder="default"
          hint="Leave blank to let any registered scanner claim this source. Set to a pool tag (e.g. site-amsterdam) to lock it to scanners in that pool."
        />
        <label className="flex items-start gap-2 text-sm text-fg cursor-pointer select-none">
          <input
            type="checkbox"
            checked={isRemovable}
            onChange={(e) => {
              removableTouched.current = true;
              setIsRemovable(e.target.checked);
            }}
            className="mt-0.5 h-4 w-4 rounded border-line text-blue-600 focus:ring-blue-400"
          />
          <span>
            <span className="font-medium">Intermittently available</span>
            <span className="block text-xs text-fg-muted mt-0.5">
              External / removable storage that may be unplugged or
              whose remote may be offline. The Sources page surfaces a
              separate "reachable / unmounted" indicator and avoids
              auto-failing the source when a scan can't reach it.
            </span>
          </span>
        </label>

        {testResult && (
          <div
            className={`rounded-md p-2 text-xs ${
              testResult.ok
                ? testResult.warn
                  ? "bg-amber-50 text-amber-900 dark:bg-amber-500/10 dark:text-amber-300"
                  : "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300"
                : "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300"
            }`}
            role="status"
          >
            {testResult.ok ? (
              <>
                {testResult.tier
                  ? `Connection OK · validated via ${testResult.tier}`
                  : "Connection OK"}
                {testResult.warn && (
                  <p className="mt-1 text-[11px] text-amber-800">{testResult.warn}</p>
                )}
              </>
            ) : (
              `${testResult.step ?? "error"}: ${testResult.error ?? "unknown"}`
            )}
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
