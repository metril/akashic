import { cn } from "./cn";

/**
 * Named icon registry. Icons are rendered as a single `<path d=…>` inside
 * a 24×24 stroked SVG. To add an icon, add a path here and reference it
 * by name. For one-off icons that don't justify a registry entry, pass
 * `path` directly.
 */
const iconPaths = {
  // Navigation
  "dashboard": "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  "folder": "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z",
  "search": "M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z",
  "duplicates": "M9 9h10v10H9zM5 5h10v10",
  "analytics": "M3 21h18M5 21V10m4 11V4m4 17v-7m4 7V8m4 13v-3",
  "sources": "M3 7h18M3 12h18M3 17h18",
  "settings": "M12 1.5a2.5 2.5 0 011.95 4.06l1.04 1.81a8 8 0 011.97 0l1.04-1.81a2.5 2.5 0 11-1.95 4.06l-1.04 1.81a8 8 0 010 1.96l1.04 1.81a2.5 2.5 0 11-4.06 1.95l-1.81-1.04a8 8 0 01-1.96 0l-1.81 1.04a2.5 2.5 0 11-1.95-4.06l-1.04-1.81a8 8 0 010-1.96L4.42 7.62A2.5 2.5 0 116.37 3.56l1.81 1.04a8 8 0 011.96 0L11.18 2.79A2.5 2.5 0 0112 1.5z",
  "audit-log": "M9 12l2 2 4-4M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9z",
  "sign-out": "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9",

  // Actions
  "download": "M12 3v12m0 0l-4-4m4 4l4-4M5 21h14",
  "arrow-left": "M19 12H5M12 19l-7-7 7-7",

  // Dashboard stat icons
  "file": "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zM14 2v6h6",
  "database": "M22 12H2M22 12a10 10 0 01-20 0M22 12a10 10 0 00-20 0",
  "box": "M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z",
} as const;

export type IconName = keyof typeof iconPaths;

interface IconProps {
  /** Named icon from the registry. */
  name?: IconName;
  /** Raw SVG path data. Use this for one-offs that don't justify a registry entry. */
  path?: string;
  className?: string;
  /** Stroke width override (default 1.75). */
  strokeWidth?: number;
  /** Accessible label. When omitted the icon is treated as decorative. */
  "aria-label"?: string;
}

export function Icon({
  name,
  path,
  className,
  strokeWidth = 1.75,
  "aria-label": ariaLabel,
}: IconProps) {
  const d = name ? iconPaths[name] : path;
  if (!d) {
    return null;
  }
  const decorative = !ariaLabel;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn("h-4 w-4 flex-shrink-0", className)}
      aria-hidden={decorative ? true : undefined}
      aria-label={ariaLabel}
      role={decorative ? undefined : "img"}
    >
      <path d={d} />
    </svg>
  );
}
