import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button, Card, CardHeader, Input, Select } from "../ui";
import { useCreateSource } from "../../hooks/useSources";
import { useCreateHost, useHosts } from "../../hooks/useHosts";
import { useTestSource, type TestSourceResult } from "../../hooks/useTestSource";
import { inferIsRemovable } from "../../lib/sources";
import { ProfilePicker } from "../credentials/ProfilePicker";
import {
  HOSTLESS_SOURCE_TYPES,
  SOURCE_TYPES,
  SOURCE_TYPE_LABELS,
  type AnyConfig,
  type SourceType,
} from "./sourceTypes";
import {
  HostFields,
  type HostConfig,
  validateHostConfig,
} from "./source-fields/HostFields";
import {
  ShareFields,
  type ShareConfig,
  validateShareConfig,
} from "./source-fields/ShareFields";

const SOURCE_TYPE_OPTIONS = SOURCE_TYPES.map((t) => ({
  value: t,
  label: SOURCE_TYPE_LABELS[t],
}));

interface AddSourceFormProps {
  onCreated?: () => void;
}

// Special sentinel for the host picker — "create a new host inline"
// rather than picking an existing one. Empty string means "no host
// selected yet"; "__new__" means the user explicitly wants to create.
const NEW_HOST = "__new__";

export function AddSourceForm({ onCreated }: AddSourceFormProps) {
  const createSource = useCreateSource();
  const createHost = useCreateHost();
  const testSource = useTestSource();
  const hostsQuery = useHosts();

  const [name, setName] = useState("");
  const [type, setType] = useState<SourceType>("local");
  // Host picker state — "" = unselected (defaults to first compatible
  // host on type change), "__new__" = create inline, otherwise host_id.
  const [hostChoice, setHostChoice] = useState<string>("");
  const [hostConfig, setHostConfig] = useState<HostConfig>({});
  const [shareConfig, setShareConfig] = useState<ShareConfig>({});
  const [preferredPool, setPreferredPool] = useState("");
  const [maxParallelScanners, setMaxParallelScanners] = useState(1);
  const [isRemovable, setIsRemovable] = useState(false);
  // v0.5.9 — optional override of the host's effective credentials.
  // Empty by default; setting this writes credential_profile_id (or
  // inline keys via shareConfig) onto the source row.
  const [overrideCredentials, setOverrideCredentials] = useState(false);
  const [credentialProfileId, setCredentialProfileId] = useState<string | null>(null);
  const removableTouched = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestSourceResult | null>(null);

  // Hosts of the same type — the picker.
  const compatibleHosts = useMemo(
    () =>
      (hostsQuery.data ?? []).filter((h) => h.type === type),
    [hostsQuery.data, type],
  );

  // On type change: reset both configs, default the host choice to
  // the first compatible host (or NEW_HOST if there are none).
  useEffect(() => {
    setShareConfig({});
    setHostConfig(type === "ssh" ? ({ auth: "password" } as HostConfig) : ({} as HostConfig));
    setTestResult(null);
    setFormError(null);
    if (HOSTLESS_SOURCE_TYPES.has(type)) {
      setHostChoice("");
    } else if (compatibleHosts.length > 0) {
      setHostChoice(compatibleHosts[0].id);
    } else {
      setHostChoice(NEW_HOST);
    }
    if (!removableTouched.current) {
      setIsRemovable(inferIsRemovable(type, {}));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type]);

  useEffect(() => {
    if (removableTouched.current) return;
    setIsRemovable(inferIsRemovable(type, shareConfig as Record<string, unknown>));
  }, [type, shareConfig]);

  // `isHostless` covers `local` (legacy) plus `paperless` (v0.7.0 Tier 3) —
  // any source whose connection_config carries everything (no Host row).
  const isHostless = HOSTLESS_SOURCE_TYPES.has(type);
  const isCreatingHost = !isHostless && hostChoice === NEW_HOST;

  const shareError = validateShareConfig(type, shareConfig);
  const hostError =
    isCreatingHost && !isHostless
      ? validateHostConfig(type as Exclude<SourceType, "local" | "paperless" | "immich">, hostConfig)
      : null;
  const validationError = shareError ?? hostError;
  const canSubmit =
    name.trim() !== "" &&
    validationError === null &&
    (isHostless || hostChoice !== "");

  // Test connection uses the provisional merged config — host fields
  // (either picked or being created inline) layered with share fields.
  const mergedForTest: Record<string, unknown> = useMemo(() => {
    if (isHostless) return { ...shareConfig };
    if (isCreatingHost) {
      return { ...hostConfig, ...shareConfig };
    }
    const picked = compatibleHosts.find((h) => h.id === hostChoice);
    return { ...(picked?.connection_config ?? {}), ...shareConfig };
  }, [isHostless, isCreatingHost, hostConfig, shareConfig, hostChoice, compatibleHosts]);

  async function handleTest() {
    setTestResult(null);
    setFormError(null);
    try {
      const r = await testSource.mutateAsync({
        type,
        connection_config: mergedForTest,
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
      setFormError(validationError ?? "Name and host required");
      return;
    }
    try {
      let host_id: string | null = null;
      if (!isHostless) {
        if (isCreatingHost) {
          // Create the host first; use the source name as the host
          // name when no separate name is collected. Conflict-handling
          // is left to the user (they can rename via /hosts after).
          const newHost = await createHost.mutateAsync({
            name: `${name} host`,
            type,
            connection_config: hostConfig as Record<string, unknown>,
          });
          host_id = newHost.id;
        } else {
          host_id = hostChoice;
        }
      }
      await createSource.mutateAsync({
        name,
        type,
        host_id,
        connection_config: shareConfig as Record<string, unknown>,
        preferred_pool: preferredPool.trim() || null,
        max_parallel_scanners: maxParallelScanners,
        is_removable: isRemovable,
        credential_profile_id: overrideCredentials ? credentialProfileId : null,
      });
      setName("");
      setShareConfig({});
      setHostConfig(type === "ssh" ? ({ auth: "password" } as HostConfig) : ({} as HostConfig));
      setPreferredPool("");
      setMaxParallelScanners(1);
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
          label="Source name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="My Documents"
          required
          hint='A label for this share — e.g. "Public Documents".'
        />
        <Select
          label="Type"
          value={type}
          onChange={(e) => setType(e.target.value as SourceType)}
          options={SOURCE_TYPE_OPTIONS}
        />

        {!isHostless && (
          <div className="rounded-md border border-line p-3 bg-app space-y-3">
            <Select
              label="Host"
              value={hostChoice}
              onChange={(e) => setHostChoice(e.target.value)}
              options={[
                ...compatibleHosts.map((h) => ({
                  value: h.id,
                  label: h.name,
                })),
                { value: NEW_HOST, label: "+ Create new host inline" },
              ]}
            />
            <p className="text-[11px] text-fg-muted -mt-1">
              Reuse credentials across many shares on the same server.
            </p>
            {hostChoice && hostChoice !== NEW_HOST && (
              // Deep-link into the Hosts page with the Discover panel
              // pre-expanded for the selected host. Lets the user batch-
              // add multiple shares without filling this form N times.
              <Link
                to={`/hosts?host=${hostChoice}&discover=1`}
                className="inline-block text-[11px] text-accent-600 hover:underline"
              >
                Or discover all shares on this host →
              </Link>
            )}
            {isCreatingHost && (
              <div className="pt-2 border-t border-line">
                <p className="text-xs text-fg-muted mb-2">
                  New host will be saved alongside this source.
                </p>
                <HostFields
                  type={type as Exclude<SourceType, "local" | "paperless" | "immich">}
                  value={hostConfig}
                  onChange={setHostConfig}
                />
              </div>
            )}
          </div>
        )}

        <div>
          {!isHostless && (
            <p className="text-xs uppercase tracking-wide text-fg-subtle mb-1">
              Share details
            </p>
          )}
          <ShareFields type={type} value={shareConfig} onChange={setShareConfig} />
        </div>

        {!isHostless && hostChoice && hostChoice !== NEW_HOST && (
          <details
            className="border border-line rounded p-3"
            open={overrideCredentials}
            onToggle={(e) => setOverrideCredentials((e.target as HTMLDetailsElement).open)}
          >
            <summary className="cursor-pointer text-sm font-medium text-fg">
              Override host credentials for this share
            </summary>
            <p className="text-xs text-fg-muted mt-2 mb-3">
              Optional. Pick a different credential profile, or leave on
              "Inline" and set per-field values via the host you'd already
              configured. Most-specific wins: source inline &gt; source
              profile &gt; host inline &gt; host profile.
            </p>
            <ProfilePicker
              type={type as Exclude<SourceType, "local" | "paperless" | "immich">}
              value={credentialProfileId}
              onChange={setCredentialProfileId}
            />
          </details>
        )}

        <Input
          label="Preferred scanner pool"
          value={preferredPool}
          onChange={(e) => setPreferredPool(e.target.value)}
          placeholder="default"
          hint="Leave blank to let any registered scanner claim this source. Set to a pool tag (e.g. site-amsterdam) to lock it to scanners in that pool."
        />
        <Input
          label="Max parallel scanners"
          type="number"
          value={maxParallelScanners}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            if (Number.isFinite(n) && n >= 1 && n <= 16) {
              setMaxParallelScanners(n);
            }
          }}
          hint="Cap (1–16) on cooperating scanners per scan. Default 1 = one scanner walks the whole tree. Higher values let scanners share work via the work-units queue (scanner-side support lands in a follow-up release)."
        />
        <label className="flex items-start gap-2 text-sm text-fg cursor-pointer select-none">
          <input
            type="checkbox"
            checked={isRemovable}
            onChange={(e) => {
              removableTouched.current = true;
              setIsRemovable(e.target.checked);
            }}
            className="mt-0.5 h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
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
            loading={createSource.isPending || createHost.isPending}
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

// AnyConfig is intentionally re-exported as a no-op to silence
// downstream imports that still reference it.
export type { AnyConfig };
