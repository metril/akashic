import { useId } from "react";

interface InheritableNumberFieldProps {
  label: string;
  /** null = inherit (from the host, or the built-in default). */
  value: number | null;
  onChange: (v: number | null) => void;
  min: number;
  max: number;
  /** Value to seed the input with when the user switches off inherit
   *  mode — the built-in default for this control. */
  fallback: number;
  /** Checkbox label: "Inherit from host" for a host-backed source,
   *  "Use the built-in default" for a host or a hostless source. */
  inheritLabel: string;
  /** One-line description rendered under the field. */
  hint: string;
}

/**
 * A scan-control field whose value can be either an explicit integer
 * or NULL ("inherit"). NULL is offered through a checkbox; unchecking
 * it reveals a number input seeded with `fallback`.
 *
 * The number input deliberately accepts any finite integer on every
 * keystroke (no in-handler range gate) — a min like 100 would
 * otherwise trap the user mid-type on the "1"/"10" prefixes. The
 * range is advisory here and authoritative on the API, which 400s a
 * bad value with a clear message.
 */
export function InheritableNumberField({
  label,
  value,
  onChange,
  min,
  max,
  fallback,
  inheritLabel,
  hint,
}: InheritableNumberFieldProps) {
  const id = useId();
  const inheriting = value == null;
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-fg mb-1">
        {label}
      </label>
      <label className="flex items-center gap-2 text-sm text-fg cursor-pointer select-none mb-1.5">
        <input
          type="checkbox"
          checked={inheriting}
          onChange={(e) => onChange(e.target.checked ? null : fallback)}
          className="h-4 w-4 rounded border-line text-accent-600 focus:ring-accent-400"
        />
        <span>{inheritLabel}</span>
      </label>
      {!inheriting && (
        <input
          id={id}
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(fallback);
              return;
            }
            const n = parseInt(raw, 10);
            if (Number.isFinite(n)) onChange(n);
          }}
          className="w-full rounded-md border border-line px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent-400 focus:border-accent-400"
        />
      )}
      <p className="text-[11px] text-fg-muted mt-1">{hint}</p>
    </div>
  );
}
