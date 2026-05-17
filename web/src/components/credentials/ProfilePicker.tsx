import { Link } from "react-router-dom";
import { Select } from "../ui";
import {
  useCredentialProfiles,
  type CredentialProfileSummary,
} from "../../hooks/useCredentialProfiles";

interface Props {
  type: CredentialProfileSummary["type"];
  /** null = inline credentials only (no profile attached). */
  value: string | null;
  onChange: (id: string | null) => void;
  label?: string;
  hint?: string;
  /**
   * Label for the `null` option. Defaults to "Inline credentials
   * (defined here)". For a host-backed source, where leaving the
   * picker on `null` means "inherit the host's credentials" rather
   * than "define them here", pass a host-aware label so the option
   * is honest about where the credentials actually come from.
   * v0.31.4.
   */
  inheritLabel?: string;
}

const INLINE = "__inline__" as const;

/**
 * Dropdown of compatible credential profiles for a given host /
 * source type, plus an "(inline credentials)" option for the
 * traditional flow. Helper line below the dropdown links to
 * /settings/credentials so the user can create a new one without
 * leaving the form context.
 */
export function ProfilePicker({
  type, value, onChange, label = "Credentials", hint, inheritLabel,
}: Props) {
  const profiles = useCredentialProfiles(type);
  const options = [
    { value: INLINE, label: inheritLabel ?? "Inline credentials (defined here)" },
    ...(profiles.data ?? []).map((p) => ({
      value: p.id,
      label: p.name + (p.description ? ` — ${p.description}` : ""),
    })),
  ];

  return (
    <div className="space-y-1">
      <Select
        label={label}
        value={value ?? INLINE}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === INLINE ? null : v);
        }}
        options={options}
      />
      {hint && <p className="text-[11px] text-fg-muted">{hint}</p>}
      <p className="text-[11px] text-fg-muted">
        Need a new one?{" "}
        <Link
          to="/settings/credentials"
          className="font-medium text-accent-600 hover:text-accent-700 underline"
        >
          Manage credential profiles →
        </Link>
      </p>
    </div>
  );
}
