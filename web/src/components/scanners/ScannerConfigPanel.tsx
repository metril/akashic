import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { Button, Input } from "../ui";

export interface ScannerOverrides {
  tika_url: string | null;
  extract_workers: number | null;
  max_concurrent_units: number | null;
}

export interface ScannerEffectiveConfig {
  tika_url: string;
  extract_workers: number;
  max_concurrent_units: number;
}

// null = inherit. For tika_url, "" = extraction explicitly disabled.
export interface ConfigDraft {
  tika_url: string | null;
  extract_workers: string | null;
  max_concurrent_units: string | null;
}

export interface ConfigErrors {
  tika_url?: string;
  extract_workers?: string;
  max_concurrent_units?: string;
}

export function draftFromOverrides(o: ScannerOverrides): ConfigDraft {
  return {
    tika_url: o.tika_url,
    extract_workers: o.extract_workers == null ? null : String(o.extract_workers),
    max_concurrent_units:
      o.max_concurrent_units == null ? null : String(o.max_concurrent_units),
  };
}

function validateInt(v: string | null, min: number, max: number): string | undefined {
  if (v == null) return undefined;
  const t = v.trim();
  if (!/^\d+$/.test(t)) return "Enter a whole number.";
  const n = Number(t);
  if (n < min || n > max) return `Must be between ${min} and ${max}.`;
  return undefined;
}

export function validateDraft(d: ConfigDraft): ConfigErrors {
  const e: ConfigErrors = {};
  if (d.tika_url != null) {
    const t = d.tika_url.trim();
    if (t && !/^https?:\/\//.test(t)) e.tika_url = "Must start with http:// or https://.";
    else if (t.length > 512) e.tika_url = "At most 512 characters.";
  }
  const w = validateInt(d.extract_workers, 1, 64);
  if (w) e.extract_workers = w;
  const u = validateInt(d.max_concurrent_units, 1, 32);
  if (u) e.max_concurrent_units = u;
  return e;
}

export function isDirty(d: ConfigDraft, o: ScannerOverrides): boolean {
  const i = draftFromOverrides(o);
  return (
    d.tika_url !== i.tika_url ||
    d.extract_workers !== i.extract_workers ||
    d.max_concurrent_units !== i.max_concurrent_units
  );
}

/** Build the PATCH body: changed fields only; reverted-to-inherit → clear_*. */
export function buildPatch(d: ConfigDraft, o: ScannerOverrides): Record<string, unknown> {
  const i = draftFromOverrides(o);
  const body: Record<string, unknown> = {};
  if (d.tika_url !== i.tika_url) {
    if (d.tika_url == null) body.clear_tika_url = true;
    else body.tika_url = d.tika_url.trim();
  }
  if (d.extract_workers !== i.extract_workers) {
    if (d.extract_workers == null) body.clear_extract_workers = true;
    else body.extract_workers = Number(d.extract_workers.trim());
  }
  if (d.max_concurrent_units !== i.max_concurrent_units) {
    if (d.max_concurrent_units == null) body.clear_max_concurrent_units = true;
    else body.max_concurrent_units = Number(d.max_concurrent_units.trim());
  }
  return body;
}

function UseDefault({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <Button size="sm" variant="ghost" type="button" onClick={onClick} disabled={disabled}>
      Use default
    </Button>
  );
}

export function ScannerConfigPanel({
  scannerId, overrides, effective,
}: {
  scannerId: string;
  overrides: ScannerOverrides;
  effective: ScannerEffectiveConfig;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<ConfigDraft>(() => draftFromOverrides(overrides));
  const errors = validateDraft(draft);
  const dirty = isDirty(draft, overrides);
  const hasErrors = Object.keys(errors).length > 0;

  const saveMut = useMutation<unknown, Error, Record<string, unknown>>({
    mutationFn: (body) => api.patch(`/scanners/${scannerId}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanners"] }),
  });

  const set = (patch: Partial<ConfigDraft>) => {
    saveMut.reset();
    setDraft((d) => ({ ...d, ...patch }));
  };
  const text = (v: string) => (v === "" ? null : v);

  const tikaDisabled = draft.tika_url === "";
  const tikaInherit = effective.tika_url
    ? `inherits: ${effective.tika_url}`
    : "inherits: extraction disabled";

  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-xs font-medium text-fg-muted hover:text-fg">
        Configuration
      </summary>
      <form
        className="mt-3 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty && !hasErrors) saveMut.mutate(buildPatch(draft, overrides));
        }}
      >
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Input
              label="Tika URL"
              value={draft.tika_url ?? ""}
              onChange={(e) => set({ tika_url: text(e.target.value) })}
              placeholder={tikaInherit}
              error={errors.tika_url}
              hint={
                tikaDisabled
                  ? "Text extraction is disabled on this scanner."
                  : draft.tika_url == null
                    ? tikaInherit
                    : undefined
              }
            />
          </div>
          <Button
            size="sm" variant="ghost" type="button"
            onClick={() => set({ tika_url: "" })}
            disabled={tikaDisabled}
          >
            Disable
          </Button>
          <UseDefault
            onClick={() => set({ tika_url: null })}
            disabled={draft.tika_url == null}
          />
        </div>
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Input
              label="Extract workers"
              inputMode="numeric"
              value={draft.extract_workers ?? ""}
              onChange={(e) => set({ extract_workers: text(e.target.value) })}
              placeholder={`inherits: ${effective.extract_workers}`}
              error={errors.extract_workers}
              hint="1–64"
            />
          </div>
          <UseDefault
            onClick={() => set({ extract_workers: null })}
            disabled={draft.extract_workers == null}
          />
        </div>
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Input
              label="Max concurrent units"
              inputMode="numeric"
              value={draft.max_concurrent_units ?? ""}
              onChange={(e) => set({ max_concurrent_units: text(e.target.value) })}
              placeholder={`inherits: ${effective.max_concurrent_units}`}
              error={errors.max_concurrent_units}
              hint="1–32"
            />
          </div>
          <UseDefault
            onClick={() => set({ max_concurrent_units: null })}
            disabled={draft.max_concurrent_units == null}
          />
        </div>
        <p className="text-xs text-fg-muted">Applied on the scanner's next heartbeat.</p>
        {saveMut.isError && (
          <p className="text-xs text-rose-700 dark:text-rose-300" role="alert">
            {saveMut.error.message || "Couldn't save configuration."}
          </p>
        )}
        {saveMut.isSuccess && !dirty && (
          <p className="text-xs text-emerald-700 dark:text-emerald-300" role="status">
            Saved.
          </p>
        )}
        <div className="flex gap-2">
          <Button
            size="sm" type="submit"
            disabled={!dirty || hasErrors}
            loading={saveMut.isPending}
          >
            Save
          </Button>
          <Button
            size="sm" variant="secondary" type="button"
            disabled={!dirty || saveMut.isPending}
            onClick={() => set({ ...draftFromOverrides(overrides) })}
          >
            Reset
          </Button>
        </div>
      </form>
    </details>
  );
}
