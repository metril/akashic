import type { ScopeGroup } from "../../lib/scopes";
import { forbiddenWithSourceLimit } from "../../lib/scopes";

interface ScopePickerProps {
  groups: ScopeGroup[];
  selected: ReadonlySet<string>;
  onToggle: (scope: string) => void;
  /** Token is limited to sources: write scopes on other resources are unavailable. */
  sourceLimited: boolean;
}

/** Scope checkboxes grouped by resource, labelled with the catalog descriptions. */
export function ScopePicker({ groups, selected, onToggle, sourceLimited }: ScopePickerProps) {
  return (
    <div className="space-y-3">
      {sourceLimited && (
        <p className="text-xs text-fg-muted">
          A token limited to sources can only hold scopes for sources, scans and tags writes, so other write scopes are
          disabled.
        </p>
      )}
      {groups.map((g) => (
        <fieldset key={g.resource} className="space-y-1">
          <legend className="text-xs font-medium uppercase tracking-wide text-fg-subtle">{g.resource}</legend>
          {g.scopes.map((s) => {
            const disabled = sourceLimited && forbiddenWithSourceLimit(s.name);
            return (
              <label
                key={s.name}
                className={`flex items-start gap-2 text-sm ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                title={disabled ? "Not available for a token limited to sources" : undefined}
              >
                <input
                  type="checkbox"
                  className="mt-1 size-3.5"
                  checked={selected.has(s.name)}
                  disabled={disabled}
                  onChange={() => onToggle(s.name)}
                  aria-label={s.name}
                />
                <span>
                  <span className="font-mono text-xs text-fg">{s.name}</span>
                  <span className="block text-xs text-fg-muted">{s.description}</span>
                </span>
              </label>
            );
          })}
        </fieldset>
      ))}
    </div>
  );
}
