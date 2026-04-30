import { useTheme, type ThemeMode } from "../../hooks/useTheme";
import { cn } from "./cn";

// Tri-state cycle: system → light → dark → system. The button shows the
// icon for the *currently active* resolved theme (sun in light, moon in
// dark) plus a small "system" badge when mode is system, so the user
// can tell apart "I forced dark" from "system happens to be dark right now."
const NEXT_MODE: Record<ThemeMode, ThemeMode> = {
  system: "light",
  light: "dark",
  dark: "system",
};

const LABEL: Record<ThemeMode, string> = {
  system: "System theme (click to switch to light)",
  light: "Light theme (click to switch to dark)",
  dark: "Dark theme (click to switch to system)",
};

function SunIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
    </svg>
  );
}

export function ThemeToggle({ className }: { className?: string }) {
  const { mode, resolved, setMode } = useTheme();
  const Icon = resolved === "dark" ? MoonIcon : SunIcon;
  return (
    <button
      type="button"
      onClick={() => setMode(NEXT_MODE[mode])}
      title={LABEL[mode]}
      aria-label={LABEL[mode]}
      className={cn(
        "relative inline-flex items-center justify-center h-9 w-9 rounded-md",
        "text-fg-muted hover:text-fg hover:bg-surface-muted",
        "transition-colors",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-1",
        className,
      )}
    >
      <Icon className="h-[18px] w-[18px]" />
      {mode === "system" && (
        <span
          className="absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full bg-accent-500 ring-2 ring-white"
          aria-hidden
        />
      )}
    </button>
  );
}
