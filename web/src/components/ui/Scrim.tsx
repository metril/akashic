interface ScrimProps {
  onClick?: () => void;
  className?: string;
}

/**
 * v0.5.11 — single source of truth for modal scrims. Replaces the
 * `bg-gray-900/55` literal scattered across ModalShell, CommandPalette,
 * and Drawer with one `bg-scrim` token (defined in tailwind.config.js).
 * Edit the token to retheme every overlay at once.
 */
export function Scrim({ onClick, className }: ScrimProps) {
  return (
    <div
      className={`absolute inset-0 bg-scrim ${className ?? ""}`}
      onClick={onClick}
      aria-hidden="true"
    />
  );
}
