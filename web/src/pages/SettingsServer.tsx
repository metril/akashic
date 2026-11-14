import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, CardHeader, Input, SectionState, Select } from "../components/ui";
import {
  useServerSettings,
  type ServerSettingDef,
  type ServerSettingGroup,
  type ServerSettingValue,
} from "../hooks/useServerSetting";

type Draft = Record<string, ServerSettingValue>;

/** Initial editable value: secrets start empty (masked value never edited). */
function initial(def: ServerSettingDef): ServerSettingValue {
  if (def.secret) return "";
  if (def.value === null || def.value === undefined) {
    if (def.type === "list[str]") return [];
    if (def.type === "dict[str,int]") return {};
    if (def.type === "bool") return false;
    return "";
  }
  return def.value;
}

function same(a: ServerSettingValue, b: ServerSettingValue): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Pull a readable message out of the API's 422 detail (string or JSON). */
function parseError(msg: string): { general: string; byKey: Record<string, string> } {
  const byKey: Record<string, string> = {};
  try {
    const parsed = JSON.parse(msg);
    const items = Array.isArray(parsed) ? parsed : parsed?.errors ?? parsed?.detail;
    if (Array.isArray(items)) {
      const rest: string[] = [];
      for (const it of items) {
        const loc: unknown[] = Array.isArray(it?.loc) ? it.loc : [];
        const key = loc.map(String).find((p) => p !== "body" && p !== "values");
        const m = String(it?.msg ?? it?.message ?? JSON.stringify(it));
        if (key) byKey[key] = m;
        else rest.push(m);
      }
      return { general: rest.join("; "), byKey };
    }
    if (parsed && typeof parsed === "object") {
      for (const [k, v] of Object.entries(parsed)) byKey[k] = String(v);
      return { general: "", byKey };
    }
  } catch {
    /* plain string */
  }
  return { general: msg, byKey };
}

function Toggle({
  checked, onChange, label,
}: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 ${
        checked ? "bg-accent-600" : "bg-line"
      }`}
    >
      <span
        className={`inline-block size-4 rounded-full bg-white transition-transform ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

function DictEditor({
  value, onChange,
}: { value: Record<string, number>; onChange: (v: Record<string, number>) => void }) {
  const rows = Object.entries(value);
  return (
    <div className="space-y-2">
      {rows.map(([k, v], i) => (
        <div key={i} className="flex gap-2 items-center">
          <Input
            aria-label="key"
            value={k}
            onChange={(e) => {
              const next = rows.map(([rk, rv], j) => (j === i ? [e.target.value, rv] : [rk, rv]));
              onChange(Object.fromEntries(next));
            }}
          />
          <Input
            aria-label="value"
            type="number"
            value={v}
            onChange={(e) => onChange({ ...value, [k]: Number(e.target.value) })}
          />
          <Button
            size="sm"
            variant="secondary"
            type="button"
            onClick={() => {
              const next = { ...value };
              delete next[k];
              onChange(next);
            }}
          >
            Remove
          </Button>
        </div>
      ))}
      <Button
        size="sm"
        variant="secondary"
        type="button"
        onClick={() => onChange({ ...value, "": 0 })}
        disabled={"" in value}
      >
        Add row
      </Button>
    </div>
  );
}

function Field({
  def, value, error, onChange,
}: {
  def: ServerSettingDef;
  value: ServerSettingValue;
  error?: string;
  onChange: (v: ServerSettingValue) => void;
}) {
  const id = `ss-${def.key}`;
  let control: React.ReactNode;
  switch (def.type) {
    case "bool":
      control = <Toggle checked={value === true} onChange={onChange} label={def.key} />;
      break;
    case "int":
      control = (
        <Input
          id={id}
          type="number"
          value={value as number | string}
          onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        />
      );
      break;
    case "enum":
      control = (
        <Select
          id={id}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          options={(def.choices ?? []).map((c) => ({ value: c, label: c }))}
        />
      );
      break;
    case "list[str]":
      control = (
        <textarea
          id={id}
          rows={Math.min(8, Math.max(3, (value as string[]).length + 1))}
          value={(value as string[]).join("\n")}
          onChange={(e) => onChange(e.target.value.split("\n"))}
          placeholder="One per line"
          className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-fg focus:outline-none focus:ring-2 focus:ring-accent-500/30 focus:border-accent-500"
        />
      );
      break;
    case "dict[str,int]":
      control = <DictEditor value={value as Record<string, number>} onChange={onChange} />;
      break;
    default:
      control = (
        <Input
          id={id}
          type={def.secret ? "password" : "text"}
          autoComplete="off"
          value={String(value ?? "")}
          placeholder={def.secret ? (def.set ? "•••• (set — type to replace)" : "Not set") : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
  return (
    <div className="py-3 grid grid-cols-1 md:grid-cols-5 gap-x-6 gap-y-2">
      <div className="md:col-span-2">
        <label htmlFor={id} className="text-sm font-medium text-fg font-mono">
          {def.key}
        </label>
        {def.restart && (
          <Badge variant="info" className="ml-2">restart required</Badge>
        )}
        <p className="text-xs text-fg-muted mt-1">{def.description}</p>
      </div>
      <div className="md:col-span-3">
        {control}
        {def.secret && def.set && (
          <button
            type="button"
            className="mt-1 text-xs text-fg-muted underline"
            onClick={() => onChange(value === null ? "" : null)}
          >
            {value === null ? "Keep current value" : "Clear stored value"}
          </button>
        )}
        {def.secret && def.set && value === null && (
          <p className="text-xs text-amber-600 mt-1">Will be cleared on save.</p>
        )}
        {error && <p className="text-xs text-rose-600 mt-1">{error}</p>}
      </div>
    </div>
  );
}

function GroupCard({ group }: { group: ServerSettingGroup }) {
  const { save } = useServerSettings();
  const base = useMemo<Draft>(
    () => Object.fromEntries(group.settings.map((s) => [s.key, initial(s)])),
    [group],
  );
  const [draft, setDraft] = useState<Draft>(base);
  const [errors, setErrors] = useState<{ general: string; byKey: Record<string, string> }>({
    general: "", byKey: {},
  });
  useEffect(() => setDraft(base), [base]);

  // Secret clear is encoded as `null` in the draft; map to API semantics below.
  const dirtyKeys = group.settings
    .filter((s) => !same(draft[s.key], base[s.key]))
    .map((s) => s.key);

  async function onSave() {
    const values: Record<string, ServerSettingValue> = {};
    for (const s of group.settings) {
      if (!dirtyKeys.includes(s.key)) continue;
      let v = draft[s.key];
      if (s.secret && v === null) v = "";            // "" clears
      if (s.type === "list[str]") v = (v as string[]).map((x) => x.trim()).filter(Boolean);
      if (s.type === "dict[str,int]") {
        v = Object.fromEntries(Object.entries(v as Record<string, number>).filter(([k]) => k.trim()));
      }
      values[s.key] = v;
    }
    setErrors({ general: "", byKey: {} });
    try {
      await save.mutateAsync(values);
      toast.success(`${group.label} settings saved`);
    } catch (e) {
      setErrors(parseError(e instanceof Error ? e.message : "Save failed"));
    }
  }

  return (
    <Card padding="md">
      <CardHeader title={group.label} />
      <div className="divide-y divide-line-subtle">
        {group.settings.map((s) => (
          <Field
            key={s.key}
            def={s}
            value={draft[s.key]}
            error={errors.byKey[s.key]}
            onChange={(v) => setDraft((d) => ({ ...d, [s.key]: v }))}
          />
        ))}
      </div>
      {errors.general && <p className="text-xs text-rose-600 mt-2">{errors.general}</p>}
      <div className="flex justify-end gap-2 mt-3">
        <Button
          variant="secondary"
          size="sm"
          disabled={dirtyKeys.length === 0 || save.isPending}
          onClick={() => { setDraft(base); setErrors({ general: "", byKey: {} }); }}
        >
          Reset
        </Button>
        <Button
          size="sm"
          disabled={dirtyKeys.length === 0}
          loading={save.isPending}
          onClick={onSave}
        >
          Save
        </Button>
      </div>
    </Card>
  );
}

export default function SettingsServer() {
  const { query } = useServerSettings();
  const groups = query.data?.groups ?? [];
  return (
    <section aria-labelledby="settings-server-heading" className="space-y-5">
      <header>
        <h2 id="settings-server-heading" className="sr-only">Server</h2>
        <p className="text-sm text-fg-muted">
          Server-wide runtime settings. Changes apply to the whole deployment;
          settings marked “restart required” take effect after the API restarts.
        </p>
      </header>
      <SectionState
        loading={query.isLoading}
        error={query.isError ? query.error : undefined}
        empty={groups.length === 0}
        emptyTitle="No settings"
        emptyMessage="The server exposes no configurable settings."
      >
        {groups.map((g) => <GroupCard key={g.key} group={g} />)}
      </SectionState>
    </section>
  );
}
